"""Tests that pin the public behavior the agent must preserve."""
from billing import (
    aggregate,
    find_first_overdue,
    format_invoice_line,
    format_summary,
    render_receipt,
    split_address,
)


def test_format_invoice_line():
    line = format_invoice_line({"name": "Widget"}, 3, 4.5)
    assert line == "Widget x 3 = $13.50"


def test_format_summary():
    assert format_summary("Acme", 99.999) == "Customer Acme owes $100.00"


def test_aggregate():
    items = [
        {"category": "tools", "amount": 10.0},
        {"category": "tools", "amount": 5.5},
        {"category": "food", "amount": 7.25},
    ]
    out = aggregate(items)
    assert out["tools"] == 15.5
    assert out["food"] == 7.25


def test_find_first_overdue_present():
    invs = [{"status": "paid"}, {"status": "overdue", "id": "INV-2"}]
    found = find_first_overdue(invs)
    assert found is not None
    assert found["id"] == "INV-2"


def test_find_first_overdue_absent():
    assert find_first_overdue([{"status": "paid"}]) is None


def test_split_address():
    a, b, c = split_address("1 Main St, Springfield, IL")
    assert a == "1 Main St"
    assert b == "Springfield"
    assert c == "IL"


def test_split_address_short():
    a, b, c = split_address("1 Main St")
    assert a == "1 Main St"
    assert b == ""
    assert c == ""


def test_render_receipt():
    out = render_receipt("Acme", [("Widget", 3, 4.5), ("Gizmo", 1, 2.0)])
    assert out.startswith("Receipt for Acme")
    assert "Total: $15.50" in out
