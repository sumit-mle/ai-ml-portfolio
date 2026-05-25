"""Golden expectations per vendor.

What we assert per vendor:
  - exact set of doc kinds expected
  - which ones must end up `found`
  - which (if any) we expect `missing`

This makes the eval truthful: if `terra-data` is supposed to have a
missing COI, the test passes only when the agent reports that exact gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class VendorExpectation:
    vendor_id: str
    must_be_found: tuple[str, ...]                 # kinds expected `found`
    expected_missing: tuple[str, ...] = ()         # kinds expected `missing`


GOLDEN: list[VendorExpectation] = [
    VendorExpectation(
        vendor_id="acme-cloud",
        must_be_found=("soc2_report", "iso27001_certificate", "w9_form", "certificate_of_insurance"),
    ),
    VendorExpectation(
        vendor_id="sentinel-security",
        must_be_found=("soc2_report", "iso27001_certificate", "w9_form", "certificate_of_insurance"),
    ),
    VendorExpectation(
        vendor_id="terra-data",
        must_be_found=("soc2_report", "w9_form"),
        expected_missing=("certificate_of_insurance",),
    ),
]


def all_expectations() -> list[VendorExpectation]:
    return list(GOLDEN)
