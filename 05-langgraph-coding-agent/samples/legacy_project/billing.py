"""Legacy billing module — written in 2017 idioms.

Lots of %-format strings, .format(), typing.List/Dict/Optional. Behavior
should be unchanged after modernization.
"""
from typing import Dict, List, Optional, Tuple


def format_invoice_line(item: Dict[str, str], qty: int, price: float) -> str:
    return "%s x %d = $%.2f" % (item["name"], qty, qty * price)


def format_summary(name: str, total: float) -> str:
    return "Customer {} owes ${:.2f}".format(name, total)  # noqa: comment to ensure we still preserve formatting


def aggregate(items: List[Dict[str, float]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for it in items:
        cat = it.get("category", "misc")  # type: ignore[arg-type]
        out[cat] = out.get(cat, 0.0) + float(it["amount"])
    return out


def find_first_overdue(invoices: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    for inv in invoices:
        if inv.get("status") == "overdue":
            return inv
    return None


def split_address(addr: str) -> Tuple[str, str, str]:
    parts = addr.split(",")
    parts = [p.strip() for p in parts]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def render_receipt(customer: str, lines: List[Tuple[str, int, float]]) -> str:
    out = ["Receipt for {}".format(customer)]
    total = 0.0
    for name, qty, price in lines:
        out.append("  %-20s %3d @ %.2f" % (name, qty, price))
        total += qty * price
    out.append("Total: ${:.2f}".format(total))
    return "\n".join(out)
