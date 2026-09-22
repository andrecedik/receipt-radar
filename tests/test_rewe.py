"""REWE parser tests against a synthetic fixture in the real REWE eBon format.

The fixture (``fixtures/rewe_synthetic.txt``) carries no personal data but
exercises every line shape seen on real eBons: simple, a ``Stk x`` quantity
continuation, a per-kg weight continuation, a Pfand line with the trailing
``*`` marker, a Leergut refund, and an indented per-item discount -- plus the
noise around them (card slip, tax table, TSE block, Bonus footer, the
"Sonstige Vorteile" block with its own lower-case ``Summe``)."""

from decimal import Decimal
from pathlib import Path

import pytest

from kaufland_receipts.parse_rewe import is_rewe, parse_text

FIXTURE = (Path(__file__).parent / "fixtures" / "rewe_synthetic.txt").read_text("utf-8")


def test_fixture_reconciles():
    r = parse_text(FIXTURE)
    assert r.total == Decimal("12.50")
    assert r.line_item_sum() == Decimal("12.50")
    assert r.totals_match()


def test_header_and_id():
    r = parse_text(FIXTURE)
    assert r.purchased_at.isoformat() == "2026-05-02T21:25:00"
    assert r.store.name == "REWE"
    assert r.store.street == "Teststraße 12"
    assert r.store.postal_code == "12345"
    assert r.store.city == "Teststadt"
    # Markt-Kasse-YYYYMMDD-Bon
    assert r.receipt_id == "rewe-3308-1-20260502-1770"


def test_line_shapes():
    items = {li.name: li for li in parse_text(FIXTURE).line_items}
    # simple
    assert items["TESTBROT"].quantity == Decimal("1")
    assert items["TESTBROT"].unit_price == Decimal("0.79")
    assert items["TESTBROT"].total_price == Decimal("0.79")
    assert items["TESTBROT"].tax_class == "B"
    # "Stk x" continuation: the price line comes first, the quantity after
    assert items["TESTAPFEL"].quantity == Decimal("2")
    assert items["TESTAPFEL"].unit_price == Decimal("1.79")
    assert items["TESTAPFEL"].total_price == Decimal("3.58")
    # per-kg continuation
    assert items["TESTBANANE"].quantity == Decimal("0.500")
    assert items["TESTBANANE"].unit_price == Decimal("2.50")
    assert items["TESTBANANE"].total_price == Decimal("1.25")
    assert items["TESTBANANE"].size_value == Decimal("0.500")
    assert items["TESTBANANE"].size_unit == "kg"
    # pack size embedded in the product name
    assert items["TESTLIMO 0,85L"].size_value == Decimal("0.85")
    assert items["TESTLIMO 0,85L"].size_unit == "l"
    assert items["TESTLIMO 0,85L"].tax_class == "A"
    # names without a parseable size stay unset
    assert items["TESTBROT"].size_value is None


def test_star_marker_is_stripped_and_line_kept():
    items = {li.name: li for li in parse_text(FIXTURE).line_items}
    assert items["PFAND 0,25 EURO"].total_price == Decimal("0.25")
    assert items["PFAND 0,25 EURO"].tax_class == "A"
    assert not any(li.name.endswith("*") for li in parse_text(FIXTURE).line_items)


def test_refund_is_not_a_discount():
    items = {li.name: li for li in parse_text(FIXTURE).line_items}
    assert items["LEERGUT EINWEG"].total_price == Decimal("-0.25")
    assert items["LEERGUT EINWEG"].tax_class == "A"


def test_indented_discount_becomes_untaxed_negative_line_item():
    # Downstream (price_integrity.attach_item_discounts, the web's
    # withAttachedDiscounts) recognises a discount purely by tax_class being
    # None and folds it into the preceding product -- so a REWE discount,
    # which *is* printed with a tax letter, must still be emitted without one.
    items = parse_text(FIXTURE).line_items
    discounts = [li for li in items if li.tax_class is None]
    assert [li.name for li in discounts] == ["GRATIS Testtee"]
    assert discounts[0].total_price == Decimal("-1.99")
    # and it directly follows the product it belongs to
    idx = items.index(discounts[0])
    assert items[idx - 1].name == "TESTTEE"


def test_footer_bonus_block_is_not_parsed_as_items():
    names = [li.name for li in parse_text(FIXTURE).line_items]
    assert "Sonstige Vorteile" not in names
    assert not any("Bonus" in n for n in names)


def test_is_rewe():
    assert is_rewe(FIXTURE)
    assert is_rewe("               R E W E               \n...")
    assert is_rewe("REWE Jens Piclum oHG\n...")
    assert is_rewe("...\nSeriennnummer Kasse: REWE:10:ff:e0:08:45:89:00")
    assert not is_rewe("Kaufland - Teststraße 1\nPreis EUR\nSumme 1,00")


def test_rejects_non_rewe():
    with pytest.raises(ValueError, match="REWE"):
        parse_text("Some other shop\nSUMME EUR 5,00")
