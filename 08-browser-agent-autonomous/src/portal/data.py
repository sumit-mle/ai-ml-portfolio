"""Demo vendor inventory used by the self-hosted portal.

Three vendors with intentionally different difficulty levels:
  - acme-cloud:  textbook flow, all docs available, no MFA
  - sentinel-security: requires MFA (TOTP-style 6-digit code shown next to login)
  - terra-data:  one required doc is missing — agent must report it correctly

Real procurement systems have hundreds of vendors but the surface area is
the same. Adding a vendor = appending a row here + dropping doc PDFs in
data/portal/.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DocumentSpec:
    kind: str                         # matches evidence.EvidenceKind
    title: str
    page_path: str                    # relative path on the portal
    pdf_filename: str | None = None   # served from /downloads/<file>
    valid_until: str | None = None    # ISO date string, may be in the past for "expired"
    is_present: bool = True           # if False, page exists but no PDF link


@dataclass(frozen=True)
class VendorSpec:
    vendor_id: str
    name: str
    username: str
    password: str
    requires_mfa: bool
    mfa_code: str | None
    documents: tuple[DocumentSpec, ...] = field(default_factory=tuple)


VENDORS: dict[str, VendorSpec] = {
    "acme-cloud": VendorSpec(
        vendor_id="acme-cloud",
        name="Acme Cloud, Inc.",
        username="procurement@bigcorp.example",
        password="acme-portal-2026",
        requires_mfa=False,
        mfa_code=None,
        documents=(
            DocumentSpec(
                kind="soc2_report",
                title="SOC 2 Type II Report (FY25)",
                page_path="/docs/soc2",
                pdf_filename="acme_soc2.pdf",
                valid_until="2026-12-31",
            ),
            DocumentSpec(
                kind="iso27001_certificate",
                title="ISO 27001:2022 Certificate",
                page_path="/docs/iso27001",
                pdf_filename="acme_iso27001.pdf",
                valid_until="2027-04-30",
            ),
            DocumentSpec(
                kind="w9_form",
                title="W-9 (Tax)",
                page_path="/docs/w9",
                pdf_filename="acme_w9.pdf",
                valid_until=None,
            ),
            DocumentSpec(
                kind="certificate_of_insurance",
                title="Certificate of Insurance",
                page_path="/docs/coi",
                pdf_filename="acme_coi.pdf",
                valid_until="2026-09-30",
            ),
        ),
    ),
    "sentinel-security": VendorSpec(
        vendor_id="sentinel-security",
        name="Sentinel Security Systems Ltd.",
        username="procurement@bigcorp.example",
        password="sentinel-portal-pass",
        requires_mfa=True,
        mfa_code="424242",
        documents=(
            DocumentSpec(
                kind="soc2_report",
                title="SOC 2 Type II Report",
                page_path="/docs/soc2",
                pdf_filename="sentinel_soc2.pdf",
                valid_until="2026-08-15",
            ),
            DocumentSpec(
                kind="iso27001_certificate",
                title="ISO 27001 Certificate",
                page_path="/docs/iso27001",
                pdf_filename="sentinel_iso27001.pdf",
                valid_until="2027-01-22",
            ),
            DocumentSpec(
                kind="w9_form",
                title="W-9",
                page_path="/docs/w9",
                pdf_filename="sentinel_w9.pdf",
            ),
            DocumentSpec(
                kind="certificate_of_insurance",
                title="Insurance Certificate",
                page_path="/docs/coi",
                pdf_filename="sentinel_coi.pdf",
                valid_until="2026-11-05",
            ),
        ),
    ),
    "terra-data": VendorSpec(
        vendor_id="terra-data",
        name="Terra Data Analytics LLC",
        username="procurement@bigcorp.example",
        password="terra-data-2026",
        requires_mfa=False,
        mfa_code=None,
        documents=(
            DocumentSpec(
                kind="soc2_report",
                title="SOC 2 Type II Report",
                page_path="/docs/soc2",
                pdf_filename="terra_soc2.pdf",
                valid_until="2026-10-01",
            ),
            DocumentSpec(
                kind="w9_form",
                title="W-9",
                page_path="/docs/w9",
                pdf_filename="terra_w9.pdf",
            ),
            # Intentionally missing the COI: page exists but the PDF link is absent.
            DocumentSpec(
                kind="certificate_of_insurance",
                title="Certificate of Insurance",
                page_path="/docs/coi",
                pdf_filename=None,
                is_present=False,
            ),
        ),
    ),
}


def all_vendor_ids() -> list[str]:
    return list(VENDORS.keys())


def get_vendor(vendor_id: str) -> VendorSpec:
    if vendor_id not in VENDORS:
        raise KeyError(f"unknown vendor: {vendor_id}")
    return VENDORS[vendor_id]
