"""Tests for the format-independent core: model reconciliation, store idempotency,
and the export/rollup functions. These need no real PDF and no network."""

import json
from datetime import datetime
from decimal import Decimal

from receipt_radar.export import (
    append_price_history,
    export_web_data,
    monthly_summary_markdown,
    to_csv,
)
from receipt_radar.models import LineItem, Receipt, Store
from receipt_radar.store import ReceiptStore


def _receipt(rid="kaufland-1", total="3.50", when="2026-08-10T14:30:00") -> Receipt:
    return Receipt(
        receipt_id=rid,
        purchased_at=datetime.fromisoformat(when),
        store=Store(name="Kaufland"),
        line_items=[
            LineItem(name="MILCH", quantity=Decimal(2), unit_price=Decimal("1.00"),
                     total_price=Decimal("2.00"), tax_class="A"),
            LineItem(name="BROT", unit_price=Decimal("1.50"),
                     total_price=Decimal("1.50"), tax_class="A"),
        ],
        total=Decimal(total),
    )


def test_totals_reconcile():
    assert _receipt().totals_match()
    bad = _receipt(total="9.99")
    assert not bad.totals_match()


def test_store_is_idempotent(tmp_path):
    store = ReceiptStore(data_dir=tmp_path)
    r = _receipt()
    assert store.save(r) is True          # first write
    assert store.save(r) is False         # second write is a no-op
    assert store.has("kaufland-1")
    assert len(store.all()) == 1


def test_store_roundtrip(tmp_path):
    store = ReceiptStore(data_dir=tmp_path)
    store.save(_receipt())
    loaded = store.load("kaufland-1")
    assert loaded.total == Decimal("3.50")
    assert loaded.line_items[0].name == "MILCH"


def test_csv_has_one_row_per_line_item():
    csv_text = to_csv([_receipt()])
    # header + 2 item rows
    assert len(csv_text.strip().splitlines()) == 3
    assert "MILCH" in csv_text and "BROT" in csv_text


def test_price_history_dedupes(tmp_path):
    log = tmp_path / "prices.jsonl"
    assert append_price_history([_receipt()], log) == 2
    # re-running adds nothing
    assert append_price_history([_receipt()], log) == 0


def test_export_web_data_writes_receipts_and_copies_available_pdfs(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-fake")
    with_pdf = _receipt(rid="has-pdf")
    with_pdf.source_file = str(pdf)
    without_pdf = _receipt(rid="no-pdf")
    without_pdf.source_file = str(tmp_path / "deleted.pdf")  # never written -- gone

    web_dir = tmp_path / "web"
    count, copied = export_web_data([with_pdf, without_pdf], web_dir)

    assert count == 2
    assert copied == 1
    assert (web_dir / "public" / "pdfs" / "has-pdf.pdf").exists()
    assert not (web_dir / "public" / "pdfs" / "no-pdf.pdf").exists()

    import json
    records = json.loads((web_dir / "public" / "data" / "receipts.json").read_text())
    by_id = {r["receipt_id"]: r for r in records}
    assert by_id["has-pdf"]["pdf_available"] is True
    assert by_id["no-pdf"]["pdf_available"] is False


def test_monthly_summary_groups_by_month():
    md = monthly_summary_markdown([
        _receipt(rid="a", total="3.50", when="2026-08-10T10:00:00"),
        _receipt(rid="b", total="6.50", when="2026-08-20T10:00:00"),
        _receipt(rid="c", total="4.00", when="2026-07-01T10:00:00"),
    ])
    assert "2026-08" in md and "2026-07" in md
    assert "10.00" in md   # August total
    assert "14.00" in md   # grand total


def _milch_receipt(rid: str, when: str, total_price: str, discount: str | None = None) -> Receipt:
    """A receipt with one Milch line item (and, optionally, an attached
    per-item discount) at a fixed store location -- built directly rather
    than via the module's existing `_receipt()` helper, since that helper
    hardcodes its own MILCH/BROT line items and doesn't accept overrides."""
    line_items = [
        LineItem(name="Milch", quantity=Decimal(1), unit_price=Decimal(total_price),
                  total_price=Decimal(total_price), tax_class="B"),
    ]
    if discount is not None:
        line_items.append(LineItem(name="K Card XTRA Rabatt", total_price=Decimal(discount)))
    total = sum((li.total_price for li in line_items), Decimal(0))
    return Receipt(
        receipt_id=rid, purchased_at=datetime.fromisoformat(when),
        store=Store(name="Kaufland", street="Teststraße 1"),
        line_items=line_items, total=total,
    )


def test_export_web_data_attaches_price_verdicts(tmp_path):
    r1 = _milch_receipt("r1", "2026-01-01T10:00:00", total_price="2.00")
    r2 = _milch_receipt("r2", "2026-01-08T10:00:00", total_price="2.00")
    r3 = _milch_receipt("r3", "2026-01-15T10:00:00", total_price="2.00", discount="-0.30")

    web_dir = tmp_path / "web"
    export_web_data([r1, r2, r3], web_dir)

    data = json.loads((web_dir / "public" / "data" / "receipts.json").read_text("utf-8"))
    r3_record = next(rec for rec in data if rec["receipt_id"] == "r3")
    verdict = r3_record["line_items"][0]["price_verdict"]
    assert verdict["label"] == "genuine"
    assert verdict["median_price"] == "2.00"
    assert verdict["current_price"] == "1.70"
    # the discount line itself never gets a verdict
    assert "price_verdict" not in r3_record["line_items"][1]
    # receipts with no qualifying discount have no price_verdict key at all
    r1_record = next(rec for rec in data if rec["receipt_id"] == "r1")
    assert "price_verdict" not in r1_record["line_items"][0]
