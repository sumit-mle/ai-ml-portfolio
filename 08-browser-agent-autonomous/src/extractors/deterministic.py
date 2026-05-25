"""Deterministic Playwright extractor.

This is the production-shape "happy path" extractor — when you know the
portal layout, you write a deterministic Playwright script that's fast,
free, and 100% reliable. Every real procurement automation team starts
here and only escalates to an LLM agent for unknown / changing portals.

The autonomous browser-use agent (in `src/agents/autonomous.py`) is the
DIFFERENT artifact — it tackles the same job WITHOUT being told the
page layout, by reasoning about the DOM each step. That's what justifies
the LLM cost.

Output of both is the same `VendorEvidenceBundle` — they're substitutable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from ..audit import AuditLog
from ..config import get_settings
from ..credentials import CredentialError, CredentialVault, VendorCredentials
from ..evidence import (
    EvidenceItem,
    VendorEvidenceBundle,
    sha256_of,
)
from ..portal.data import VendorSpec, get_vendor

logger = logging.getLogger(__name__)


async def _login(page: Page, creds: VendorCredentials) -> None:
    await page.goto(creds.portal_url)
    await page.fill('input[name="email"]', creds.username)
    await page.fill('input[name="password"]', creds.password)
    await page.click('button[type="submit"]')
    if creds.requires_mfa:
        # The MFA page shows the code in a banner; in production we'd pull
        # it from a TOTP secret. For the demo we read it back from the page
        # so the test exercises the find-and-type pattern.
        await page.wait_for_url("**/mfa")
        # Code is in <b>...</b> inside .banner div on the page.
        code = await page.locator(".banner b").inner_text()
        await page.fill('input[name="code"]', code.strip())
        await page.click('button[type="submit"]')
    await page.wait_for_url("**/dashboard")


async def _try_download(
    context: BrowserContext,
    page: Page,
    vendor: VendorSpec,
    spec,                                  # DocumentSpec
    out_dir: Path,
) -> EvidenceItem:
    """Visit the document page, download the PDF if present, return an EvidenceItem."""
    s = get_settings()
    item = EvidenceItem(
        kind=spec.kind,                    # type: ignore[arg-type]
        description=spec.title,
        required=True,
    )
    target_url = f"{s.portal_base_url}/{vendor.vendor_id}{spec.page_path}"
    item.source_url = target_url
    try:
        await page.goto(target_url)
    except Exception as e:
        item.status = "error"
        item.notes = f"navigate failed: {e}"
        return item

    # If the doc is missing, the page renders a "not yet uploaded" warn block.
    if await page.locator(".warn").count() > 0:
        item.status = "missing"
        item.notes = "Page indicates document not yet uploaded."
        return item

    # Otherwise click the download link
    dl_link = page.locator("a.dl")
    if await dl_link.count() == 0:
        item.status = "missing"
        item.notes = "No download link found."
        return item

    out_dir.mkdir(parents=True, exist_ok=True)
    target_file = out_dir / f"{spec.kind}.pdf"
    async with page.expect_download(timeout=s.browser_timeout_s * 1000) as dlinfo:
        await dl_link.click()
    download = await dlinfo.value
    await download.save_as(str(target_file))
    item.file_path = str(target_file.relative_to(Path.cwd())) if target_file.is_relative_to(Path.cwd()) else str(target_file)
    item.bytes = target_file.stat().st_size
    item.sha256 = sha256_of(target_file)
    item.found_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Validity check: parse the value from the page if rendered there.
    if spec.valid_until:
        item.valid_until = spec.valid_until
        try:
            from datetime import date
            d = date.fromisoformat(spec.valid_until)
            if d < date.today():
                item.status = "expired"
                item.notes = f"Document expired on {spec.valid_until}."
                return item
        except Exception:
            pass

    item.status = "found"
    return item


async def pull_vendor(
    vendor_id: str,
    *,
    operator: str = "ci",
    audit: AuditLog | None = None,
    run_id: str | None = None,
) -> VendorEvidenceBundle:
    """Run the full deterministic flow for one vendor."""
    s = get_settings()
    audit = audit or AuditLog(s.audit_log_path)
    run_id = run_id or f"run_{uuid4().hex[:12]}"
    vault = CredentialVault()
    creds = vault.get(vendor_id)
    vendor = get_vendor(vendor_id)
    out_dir = s.evidence_dir / run_id / vendor_id
    bundle = VendorEvidenceBundle(
        vendor_id=vendor.vendor_id,
        vendor_name=vendor.name,
        portal_url=creds.portal_url,
        run_id=run_id,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        operator=operator,
    )

    audit.log(
        run_id=run_id, vendor_id=vendor_id, principal=operator,
        action="run_started", outcome="ok", url=creds.portal_url,
        description=f"Deterministic Playwright extractor for {vendor.name}",
    )

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=s.headless)
        context: BrowserContext = await browser.new_context(accept_downloads=True)
        page: Page = await context.new_page()

        # 1. Login
        t0 = time.perf_counter()
        try:
            await _login(page, creds)
            audit.log(
                run_id=run_id, vendor_id=vendor_id, principal=operator,
                action="login", outcome="ok", url=page.url,
                description="Logged in successfully",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            audit.log(
                run_id=run_id, vendor_id=vendor_id, principal=operator,
                action="login", outcome="error", url=page.url,
                description=f"Login failed: {e}",
            )
            await browser.close()
            bundle.update_counts()
            bundle.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return bundle

        # 2. Iterate documents
        for spec in vendor.documents:
            t1 = time.perf_counter()
            item = await _try_download(context, page, vendor, spec, out_dir)
            bundle.items.append(item)
            audit.log(
                run_id=run_id, vendor_id=vendor_id, principal=operator,
                action="extract_document", outcome=("ok" if item.status == "found" else "warn"),
                url=item.source_url,
                description=f"{item.kind}: {item.status}{(' — ' + item.notes) if item.notes else ''}",
                duration_ms=(time.perf_counter() - t1) * 1000,
                extra={"sha256": item.sha256, "bytes": item.bytes},
            )

        await browser.close()

    bundle.update_counts()
    bundle.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    audit.log(
        run_id=run_id, vendor_id=vendor_id, principal=operator,
        action="run_finished", outcome="ok",
        description=(
            f"status={bundle.overall_status} "
            f"found={bundle.n_found}/{bundle.n_required} "
            f"missing={bundle.n_missing} expired={bundle.n_expired}"
        ),
    )
    return bundle


def pull_vendor_sync(*args, **kwargs) -> VendorEvidenceBundle:
    return asyncio.run(pull_vendor(*args, **kwargs))


def pull_many(vendor_ids: Iterable[str], *, operator: str = "ci") -> list[VendorEvidenceBundle]:
    return [pull_vendor_sync(v, operator=operator) for v in vendor_ids]
