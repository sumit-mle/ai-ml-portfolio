"""Autonomous browser agent path using browser-use.

Same target (the self-hosted vendor portal), same output schema
(VendorEvidenceBundle), but the agent figures out the page layout from a
natural-language task description instead of hard-coded selectors.

Why both paths exist:
  - Deterministic Playwright (extractors/deterministic.py) is fast, free,
    and 100% reliable when you know the portal layout. Production teams
    start here.
  - This autonomous path is what you reach for when:
      * You're onboarding a new vendor whose portal you've never seen
      * The portal layout changed and you need a quick patch
      * Different vendors have different portals and writing one
        deterministic script per vendor doesn't scale

Both paths use the same `sensitive_data` substitution so the LLM never
sees the actual password — browser-use replaces placeholder tokens with
real values at action time.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from browser_use import Agent, BrowserProfile, BrowserSession, ChatOpenAI

from ..audit import AuditLog
from ..config import get_settings, require_openai_key
from ..credentials import CredentialVault
from ..evidence import EvidenceItem, VendorEvidenceBundle, sha256_of
from ..portal.data import get_vendor

logger = logging.getLogger(__name__)


_TASK_TEMPLATE = """\
You are a procurement compliance bot.

Goal: log into the vendor compliance portal at {portal_url} and download
every required compliance document. Return a structured list of what you
got and what was missing.

Login:
  Email: {{vendor_username}}
  Password: {{vendor_password}}
{mfa_hint}

Required documents (their TYPE, not necessarily exact title):
{doc_list}

Steps to follow:
  1. Open {portal_url}.
  2. Log in with the credentials above.
  3. After login you will land on a dashboard listing available compliance
     documents. For each REQUIRED document type:
       a. Click the document link.
       b. If a "Download PDF" link is present, download it.
       c. If the page says "not yet uploaded" (warning banner), record it
          as missing and move on.
  4. Stop when you have visited every required document page once.

Stay within {portal_url}. Do not navigate to any other host.
"""


def _format_doc_list(vendor) -> str:
    return "\n".join(
        f"  - {d.kind}: {d.title}" for d in vendor.documents
    )


def _format_mfa_hint(vendor) -> str:
    if not vendor.requires_mfa:
        return ""
    return (
        "\nThis portal uses MFA. After password login, you will see a page "
        "with a verification code shown in a banner box (it starts with "
        "'Verification code:'). Read the 6-digit code from the banner and "
        "type it into the verification input. The placeholder "
        "{vendor_mfa_code} is also available if you prefer."
    )


async def _materialize_evidence_from_downloads(
    vendor,
    downloads_dir: Path,
    out_dir: Path,
) -> list[EvidenceItem]:
    """Inspect what got downloaded and turn it into typed EvidenceItems.

    The agent saves files to a known directory; we map by filename pattern
    back to the document spec to populate the bundle correctly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[EvidenceItem] = []
    for spec in vendor.documents:
        item = EvidenceItem(
            kind=spec.kind,                                 # type: ignore[arg-type]
            description=spec.title,
            required=True,
        )
        # Look for a downloaded file that matches this spec by filename.
        candidate = None
        for p in downloads_dir.glob("*"):
            name = p.name.lower()
            if spec.pdf_filename and spec.pdf_filename.lower() in name:
                candidate = p
                break
            if spec.kind in name:
                candidate = p
                break
        if candidate and candidate.exists():
            target = out_dir / f"{spec.kind}.pdf"
            target.write_bytes(candidate.read_bytes())
            item.file_path = str(target)
            item.bytes = target.stat().st_size
            item.sha256 = sha256_of(target)
            item.found_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            item.valid_until = spec.valid_until
            item.status = "found"
        else:
            item.status = "missing"
            item.notes = "Agent did not download a matching file."
        items.append(item)
    return items


async def pull_vendor_autonomous(
    vendor_id: str,
    *,
    operator: str = "agent",
    audit: AuditLog | None = None,
    run_id: str | None = None,
) -> VendorEvidenceBundle:
    require_openai_key()
    s = get_settings()
    audit = audit or AuditLog(s.audit_log_path)
    run_id = run_id or f"agent_{uuid4().hex[:12]}"
    vault = CredentialVault()
    creds = vault.get(vendor_id)
    vendor = get_vendor(vendor_id)

    # browser-use Downloads land here; we relocate matched files to evidence_dir.
    download_dir = s.data_dir / "agent_downloads" / run_id
    download_dir.mkdir(parents=True, exist_ok=True)
    out_dir = s.evidence_dir / run_id / vendor_id

    audit.log(
        run_id=run_id, vendor_id=vendor_id, principal=operator,
        action="run_started", outcome="ok", url=creds.portal_url,
        description=f"Autonomous agent run for {vendor.name}",
    )

    task = _TASK_TEMPLATE.format(
        portal_url=creds.portal_url.rsplit("/login", 1)[0] + "/",
        doc_list=_format_doc_list(vendor),
        mfa_hint=_format_mfa_hint(vendor),
    )

    bundle = VendorEvidenceBundle(
        vendor_id=vendor.vendor_id,
        vendor_name=vendor.name,
        portal_url=creds.portal_url,
        run_id=run_id,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        operator=operator,
    )

    profile = BrowserProfile(
        headless=s.headless,
        downloads_dir=str(download_dir),
        allowed_domains=list(s.allowed_hosts),
    )
    browser_session = BrowserSession(browser_profile=profile)

    llm = ChatOpenAI(model=s.gen_model, api_key=s.openai_api_key)
    agent = Agent(
        task=task,
        llm=llm,
        browser_session=browser_session,
        sensitive_data=dict(vault.sensitive_data_map(creds)),
        max_failures=3,
    )

    step_counter = {"n": 0}

    async def on_step(_state, _output, _step_n):  # browser-use callback shape
        step_counter["n"] += 1
        try:
            url = getattr(_state, "url", None) or ""
        except Exception:
            url = ""
        audit.log(
            run_id=run_id, vendor_id=vendor_id, principal=operator,
            action="agent_step", outcome="ok", url=url,
            description=f"step {step_counter['n']}",
        )

    # Some browser-use versions accept this via constructor; older expect set_*.
    try:
        agent.register_new_step_callback = on_step  # type: ignore[attr-defined]
    except Exception:
        pass

    t0 = time.perf_counter()
    try:
        await agent.run(max_steps=s.agent_max_steps)
        audit.log(
            run_id=run_id, vendor_id=vendor_id, principal=operator,
            action="agent_done", outcome="ok",
            description=f"agent finished after {step_counter['n']} steps",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        logger.exception("Autonomous agent run failed")
        audit.log(
            run_id=run_id, vendor_id=vendor_id, principal=operator,
            action="agent_done", outcome="error",
            description=f"agent crashed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    # Translate downloads to EvidenceItems
    items = await _materialize_evidence_from_downloads(vendor, download_dir, out_dir)
    bundle.items = items
    bundle.update_counts()
    bundle.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    audit.log(
        run_id=run_id, vendor_id=vendor_id, principal=operator,
        action="run_finished", outcome="ok",
        description=(
            f"status={bundle.overall_status} "
            f"found={bundle.n_found}/{bundle.n_required} "
            f"missing={bundle.n_missing}"
        ),
    )
    return bundle


def pull_vendor_autonomous_sync(*args, **kwargs) -> VendorEvidenceBundle:
    return asyncio.run(pull_vendor_autonomous(*args, **kwargs))
