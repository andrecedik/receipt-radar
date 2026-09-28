"""EDEKA parser tests against a synthetic fixture in the real EDEKA Kassenbon
format.

The fixture (``fixtures/edeka_synthetic.txt``) carries no personal data but is
written in the order pypdf extracts real EDEKA receipts in -- which is *not*
the printed order (see the ``parse_edeka`` module docstring). It exercises
every line shape seen on real Kassenbons: simple, the unexplained ``W`` suffix
on the tax letter, a per-kg weighed item, a same-row quantity, a multi-item
Pfand line with the ``*`` no-PAYBACK marker, and a SOFORTSTORNO void -- plus
the noise around them (Coupon line, tax table, PAYBACK block, TSE block)."""

from decimal import Decimal
from pathlib import Path

import pytest

from receipt_radar.parse_edeka import is_edeka, parse_text

FIXTURE = (Path(__file__).parent / "fixtures" / "edeka_synthetic.txt").read_text("utf-8")


def test_fixture_reconciles():
    r = parse_text(FIXTURE)
    assert r.total == Decimal("13.55")
    assert r.line_item_sum() == Decimal("13.55")
    assert r.totals_match()


def test_header_and_id():
    r = parse_text(FIXTURE)
    assert r.purchased_at.isoformat() == "2026-05-02T21:25:00"
    assert r.store.name == "EDEKA"
    assert r.store.street == "Teststraße 12"
    assert r.store.postal_code == "12345"
    assert r.store.city == "Teststadt"
    # Filiale-Pos-YYYYMMDD-Bon
    assert r.receipt_id == "edeka-0200001-201-20260502-1770"


def test_line_shapes():
    items = {li.name: li for li in parse_text(FIXTURE).line_items}
    # simple
    assert items["TESTBROT"].quantity == Decimal("1")
    assert items["TESTBROT"].unit_price == Decimal("1.20")
    assert items["TESTBROT"].total_price == Decimal("1.20")
    assert items["TESTBROT"].tax_class == "A"
    # the "W" suffix is dropped, the item kept
    assert items["TESTKAESE"].tax_class == "A"
    assert items["TESTKAESE"].total_price == Decimal("2.08")
    # weighed: "kg x<weight> <unit>" comes *before* the item line in pypdf order
    assert items["TESTPAPRIKA"].quantity == Decimal("0.500")
    assert items["TESTPAPRIKA"].unit_price == Decimal("4.00")
    assert items["TESTPAPRIKA"].total_price == Decimal("2.00")
    assert items["TESTPAPRIKA"].size_value == Decimal("0.500")
    assert items["TESTPAPRIKA"].size_unit == "kg"
    # same-row quantity, extracted as "<qty>€ x <unit><name>"
    assert items["TESTTUECHER"].quantity == Decimal("2")
    assert items["TESTTUECHER"].unit_price == Decimal("0.85")
    assert items["TESTTUECHER"].total_price == Decimal("1.70")
    assert items["TESTTUECHER"].tax_class == "B"
    # quantity + W suffix + pack size in the name
    assert items["TESTCOLA 0,5l"].quantity == Decimal("3")
    assert items["TESTCOLA 0,5l"].tax_class == "B"
    assert items["TESTCOLA 0,5l"].size_value == Decimal("0.5")
    assert items["TESTCOLA 0,5l"].size_unit == "l"
    # multi-item Pfand with the "*" no-PAYBACK marker stays a taxed item
    assert items["Pfand"].quantity == Decimal("3")
    assert items["Pfand"].unit_price == Decimal("0.25")
    assert items["Pfand"].total_price == Decimal("0.75")
    assert items["Pfand"].tax_class == "B"


def test_sofortstorno_drops_the_voided_item_and_the_void_line():
    names = [li.name for li in parse_text(FIXTURE).line_items]
    assert "TESTSALAT" not in names
    assert names == [
        "TESTBROT", "TESTKAESE", "TESTFOLIE", "TESTPAPRIKA",
        "TESTTUECHER", "TESTCOLA 0,5l", "Pfand",
    ]


def test_noise_lines_are_not_items():
    names = {li.name for li in parse_text(FIXTURE).line_items}
    assert not any(n.startswith(("Coupon", "€/kg", "kg x", "----")) for n in names)


def test_is_edeka():
    assert is_edeka(FIXTURE)
    # The operator's company name heads the receipt, not "EDEKA"; the
    # Datum/Filiale/Pos/Bed/Bon column header identifies it on its own.
    assert is_edeka("Some GmbH\nDatum Uhrzeit  Filiale Pos Bed Bon\n")
    assert not is_edeka("Kaufland - Teststraße 1\nSumme 5,00")


def test_missing_total_raises_with_label():
    text = FIXTURE.replace("SUMME € 13,55", "")
    with pytest.raises(ValueError, match=r"^bad\.pdf: could not find the total"):
        parse_text(text, label="bad.pdf")


def test_missing_date_raises():
    text = FIXTURE.replace("02.05.26 21:25 0200001 201 005 1770", "")
    with pytest.raises(ValueError, match="could not find the date"):
        parse_text(text)
