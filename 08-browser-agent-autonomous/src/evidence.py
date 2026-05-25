"""Pydantic schemas for the evidence bundle.

The bundle is what the agent produces per vendor: a typed manifest of every
required document, where it was found, when it was downloaded, and a SHA-256
of the file. Compliance teams consume this; auditors verify against it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


EvidenceKind = Literal[
    "soc2_report",
    "iso27001_certificate",
    "w9_form",
    "certificate_of_insurance",
    "msa_signed",
    "data_processing_addendum",
    "other",
]

DocumentStatus = Literal["found", "missing", "expired", "error"]


class EvidenceItem(BaseModel):
    kind: EvidenceKind
    description: str = ""
    required: bool = True
    status: DocumentStatus = "missing"
    file_path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    source_url: str | None = None
    valid_until: str | None = None     # ISO date if discoverable on the portal
    notes: str = ""
    found_at: str | None = None        # ISO datetime when downloaded


class VendorEvidenceBundle(BaseModel):
    vendor_id: str
    vendor_name: str
    portal_url: str
    run_id: str
    started_at: str
    finished_at: str | None = None
    operator: str
    items: list[EvidenceItem] = Field(default_factory=list)

    # Run metrics
    n_required: int = 0
    n_found: int = 0
    n_missing: int = 0
    n_expired: int = 0
    n_errors: int = 0

    @property
    def overall_status(self) -> Literal["complete", "partial", "failed"]:
        if self.n_required and self.n_found == self.n_required:
            return "complete"
        if self.n_found == 0:
            return "failed"
        return "partial"

    def update_counts(self) -> None:
        self.n_required = sum(1 for i in self.items if i.required)
        self.n_found = sum(1 for i in self.items if i.required and i.status == "found")
        self.n_missing = sum(1 for i in self.items if i.required and i.status == "missing")
        self.n_expired = sum(1 for i in self.items if i.required and i.status == "expired")
        self.n_errors = sum(1 for i in self.items if i.status == "error")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
