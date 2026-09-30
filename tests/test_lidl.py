"""Lidl parser tests against a synthetic fixture in the shape Tesseract hands
back for a Lidl app receipt screenshot (see ``parse_lidl``).

The OCR text is plain tesseract output, so besides the format rules these
tests pin the digit-repair behaviour: the receipt prints enough redundancy
(qty x unit = line total, per-tax-class gross amounts, the grand total) to
pick out a misread digit, and a receipt that still doesn't reconcile must be
rejected rather than stored with wrong prices."""

from decimal import Decimal
from pathlib import Path

import pytest

from receipt_radar.parse_lidl import is_lidl, parse_readings, parse_text

FIXTURE = (Path(__file__).parent / "fixtures" / "lidl_synthetic.txt").read_text("utf-8")


def test_is_lidl():
    assert is_lidl(FIXTURE)
    assert not is_lidl("Kaufland - Teststr. 1\nSumme 1,00")


def test_fixture_reconciles():
    r = parse_text(FIXTURE)
    assert r.total == Decimal("7.13")
    assert r.totals_match()


def test_header_and_id():
    r = parse_text(FIXTURE)
    assert r.purchased_at.isoformat() == "2026-01-05T09:36:00"
    assert r.store.name == "Lidl"
    assert r.store.street == "Teststraße 12"
    assert r.store.postal_code == "12345"
    assert r.store.city == "Teststadt"
    # Filiale-YYYYMMDD-Bon (the Kasse digits are too unreliable under OCR)
    assert r.receipt_id == "lidl-5567-20260105-511555"


def test_line_shapes():
    items = {li.name: li for li in parse_text(FIXTURE).line_items if li.total_price > 0}
    assert items["Testbrot"].total_price == Decimal("1.20")
    assert items["Testbrot"].tax_class == "A"
    # weighed: the "<kg> x <unit> EUR/kg" detail line follows the item
    assert items["Testbanane lose"].quantity == Decimal("1.200")
    assert items["Testbanane lose"].unit_price == Decimal("1.29")
    assert items["Testbanane lose"].total_price == Decimal("1.55")
    assert items["Testbanane lose"].size_unit == "kg"
    # "<unit> x <qty> <total>"
    assert items["Testjoghurt"].quantity == Decimal("2")
    assert items["Testjoghurt"].unit_price == Decimal("0.79")
    assert items["Testjoghurt"].total_price == Decimal("1.58")
    # deposit line keeps its printed label and the B tax class
    assert items["Pfand 0,25 EM"].quantity == Decimal("2")
    assert items["Pfand 0,25 EM"].total_price == Decimal("0.50")
    assert items["Pfand 0,25 EM"].tax_class == "B"


def test_discounts_are_negative_line_items_without_tax_class():
    discounts = [li for li in parse_text(FIXTURE).line_items if li.total_price < 0]
    assert [(d.name, d.total_price, d.tax_class) for d in discounts] == [
        ("Lidl Plus Rabatt", Decimal("-0.22"), None),
        ("Preisvorteil", Decimal("-0.76"), None),
    ]


def test_discount_with_misread_leading_zero_is_read_as_below_one_euro():
    text = FIXTURE.replace("Preisvorteil -0,76", "Preisvorteil -8,76")
    r = parse_text(text)
    assert r.totals_match()
    assert [li.total_price for li in r.line_items if li.name == "Preisvorteil"] == [Decimal("-0.76")]


def test_misread_zero_in_a_price_is_repaired_from_tax_class_totals():
    text = FIXTURE.replace("Testbrot 1,20 A", "Testbrot 1,28 A")
    r = parse_text(text)
    assert r.totals_match()
    assert next(li for li in r.line_items if li.name == "Testbrot").total_price == Decimal("1.20")


def test_garbled_tax_table_row_is_repaired_from_its_own_arithmetic():
    # B row misread: 0,21 + 1,08 = 1,29 no longer holds as "8,21 1,08 1,29"
    text = FIXTURE.replace("B 19 % 0,21 1,08 1,29", "B 19 % 8,21 1,08 1,29")
    assert parse_text(text).totals_match()


def test_misread_unit_price_is_repaired_from_quantity_arithmetic():
    text = FIXTURE.replace("Testjoghurt 0,79 x 2 1,58", "Testjoghurt 6,79 x 2 1,58")
    item = next(li for li in parse_text(text).line_items if li.name == "Testjoghurt")
    assert item.unit_price == Decimal("0.79")


def test_overlong_weight_is_truncated_to_three_decimals():
    text = FIXTURE.replace("1,200 kg", "1,2008 kg")
    item = next(li for li in parse_text(text).line_items if li.name == "Testbanane lose")
    assert item.quantity == Decimal("1.200")


def test_stray_trailing_digit_on_a_price_is_dropped():
    text = FIXTURE.replace("Testbrot 1,20 A", "Testbrot 1,208 A")
    assert next(li for li in parse_text(text).line_items if li.name == "Testbrot").total_price == Decimal("1.20")


def test_unreconcilable_receipt_is_rejected():
    # An error that digit substitution cannot explain: a line the OCR dropped.
    text = FIXTURE.replace("Testbrot 1,20 A\n", "")
    with pytest.raises(ValueError, match="reconcile"):
        parse_text(text)


def test_missing_date_raises():
    with pytest.raises(ValueError, match="date"):
        parse_text(FIXTURE.replace("05.01.26 09:36", ""))


def test_compensating_errors_are_caught_by_the_printed_preisvorteil():
    # Two misreads that cancel in every tax-class sum (+0,08 and -0,08): only
    # the printed "Gesamter Preisvorteil" exposes that the discount is wrong.
    text = FIXTURE.replace("Lidl Plus Rabatt -0,22", "Lidl Plus Rabatt -0,14").replace(
        "Testkäse 2,49 A", "Testkäse 2,41 A"
    )
    with pytest.raises(ValueError, match="Preisvorteil"):
        parse_text(text)


def test_readings_must_agree_and_at_least_two_must_reconcile():
    bad = FIXTURE.replace("Testbrot 1,20 A\n", "")  # reconciles nowhere
    # the same receipt read twice, names differing only by language pack
    assert parse_readings([FIXTURE, FIXTURE.replace("Testkäse", "Testkase")]).totals_match()
    with pytest.raises(ValueError, match="only 1 of 2"):
        parse_readings([FIXTURE, bad])
    # both reconcile, but to different prices (a 2,49 vs 2,39 cheese + 0,10 more discount)
    other = FIXTURE.replace("Testkäse 2,49 A", "Testkäse 2,39 A").replace(
        "Preisvorteil -0,76", "Preisvorteil -0,66"
    ).replace("Gesamter Preisvorteil 0,98", "Gesamter Preisvorteil 0,88")
    with pytest.raises(ValueError, match="disagree"):
        parse_readings([FIXTURE, other])


def test_receipt_id_is_voted_across_readings_not_required_to_match():
    noisy = FIXTURE.replace("5567 511555/03", "55607 5115558/03")
    r = parse_readings([noisy, FIXTURE, FIXTURE])
    assert r.receipt_id == "lidl-5567-20260105-511555"
    with pytest.raises(ValueError, match="id/date"):
        parse_readings([noisy, FIXTURE.replace("5567 511555/03", "5568 511555/03")])
