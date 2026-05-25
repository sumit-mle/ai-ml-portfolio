"""Generate compliance PDFs at runtime.

We don't ship binary PDFs in the repo. Each vendor's docs are generated on
first portal startup using reportlab, into data/portal/. Same content every
run so checksums are stable.
"""
from __future__ import annotations

import logging
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .data import VENDORS, DocumentSpec, VendorSpec

logger = logging.getLogger(__name__)


def _page(out_path: Path, title: str, body_lines: list[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=LETTER, title=title)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    for ln in body_lines:
        story.append(Paragraph(ln, styles["Normal"]))
        story.append(Spacer(1, 6))
    doc.build(story)


def _doc_body(vendor: VendorSpec, spec: DocumentSpec) -> list[str]:
    lines = [
        f"<b>Vendor:</b> {vendor.name}",
        f"<b>Document:</b> {spec.title}",
        f"<b>Document type:</b> {spec.kind}",
    ]
    if spec.valid_until:
        lines.append(f"<b>Valid until:</b> {spec.valid_until}")
    lines.append(
        "This is a synthetic compliance artifact generated for the demo "
        "browser-agent project. It contains no real assertions about any "
        "real entity."
    )
    lines.append(
        f"<b>SHA-anchor:</b> {vendor.vendor_id}-{spec.kind}"
    )
    return lines


def ensure_pdfs(out_dir: Path) -> dict[str, Path]:
    """Make sure every spec.pdf_filename exists. Returns map filename -> path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for vendor in VENDORS.values():
        for spec in vendor.documents:
            if spec.pdf_filename is None or not spec.is_present:
                continue
            target = out_dir / spec.pdf_filename
            if not target.exists():
                _page(target, spec.title, _doc_body(vendor, spec))
                logger.info("Generated %s", target)
            written[spec.pdf_filename] = target
    return written
