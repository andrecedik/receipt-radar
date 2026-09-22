"""Parser tests against a synthetic fixture in the real Kaufland format.

The fixture (``fixtures/receipt_synthetic.txt``) carries no personal data but
exercises every line shape: simple, inline-quantity, two-line quantity, two-line
weight, single-line weight, a per-unit Leergut refund, a per-item loyalty
discount, a Mengenrabatt, a Pfand line, and a final Rabattaktion discount. If
the parser survives all of these and still reconciles to the printed total,
the format logic is sound."""

from decimal import Decimal
from pathlib import Path

import pytest

from kaufland_receipts.parse_pdf import is_kaufland, parse_text

FIXTURE = (Path(__file__).parent / "fixtures" / "receipt_synthetic.txt").read_text("utf-8")


def test_fixture_reconciles():
    r = parse_text(FIXTURE)
    assert r.total == Decimal("8.20")
    assert r.line_item_sum() == Decimal("8.20")
    assert r.totals_match()


def test_header_and_id():
    r = parse_text(FIXTURE)
    assert r.purchased_at.isoformat() == "2026-08-07T12:00:00"
    assert r.store.city == "Teststadt"
    assert r.store.postal_code == "12345"
    # Filiale-Kasse-YYYYMMDD-Bon
    assert r.receipt_id == "kaufland-1234-1-20260807-99999"


def test_line_shapes():
    items = {li.name: li for li in parse_text(FIXTURE).line_items}
    # inline quantity
    assert items["Testartikel Zwei"].quantity == Decimal("2")
    assert items["Testartikel Zwei"].unit_price == Decimal("1.50")
    assert items["Testartikel Zwei"].total_price == Decimal("3.00")
    # two-line quantity (name on its own line)
    assert items["Testkaese Gouda"].quantity == Decimal("3")
    assert items["Testkaese Gouda"].total_price == Decimal("3.00")
    # two-line weight
    assert items["Testgemuese kg"].quantity == Decimal("0.500")
    assert items["Testgemuese kg"].total_price == Decimal("1.00")
    assert items["Testgemuese kg"].size_value == Decimal("0.500")
    assert items["Testgemuese kg"].size_unit == "kg"
    # single-line weight (name, weight and price all on one text line --
    # pypdf occasionally merges the two logical rows this way)
    assert items["Testfrucht kg"].quantity == Decimal("0.300")
    assert items["Testfrucht kg"].total_price == Decimal("0.90")
    assert items["Testfrucht kg"].size_value == Decimal("0.300")
    assert items["Testfrucht kg"].size_unit == "kg"
    # pack size embedded in the product name
    assert items["Testartikel Eins 500g"].size_value == Decimal("500")
    assert items["Testartikel Eins 500g"].size_unit == "g"
    # names without a parseable size stay unset
    assert items["Testartikel Zwei"].size_value is None
    # tax classes: A = 19%, B = 7%
    assert items["Pfandartikel"].tax_class == "A"
    assert items["Testartikel Eins 500g"].tax_class == "B"
    # a Leergut-style refund priced per unit -- the printed unit price is
    # unsigned even though the line total is negative; the parser must flip it
    assert items["Testleergut"].quantity == Decimal("2")
    assert items["Testleergut"].unit_price == Decimal("-0.25")
    assert items["Testleergut"].total_price == Decimal("-0.50")


def test_discounts_are_negative_line_items():
    discounts = [li for li in parse_text(FIXTURE).line_items if li.tax_class is None]
    assert len(discounts) == 2  # per-item Rabatt, Mengenrabatt -- the Rabattaktion
    # block's final K Card XTRA Rabatt is threshold_coupon_discount, not a line item
    assert all(li.total_price < 0 for li in discounts)
    assert {li.name for li in discounts} == {"K Card XTRA Rabatt", "Mengenrabatt"}


def test_refund_is_not_a_discount():
    # Testleergut has a negative total but carries a tax class, so it must
    # not be picked up by the (name-agnostic) discount pattern.
    items = {li.name: li for li in parse_text(FIXTURE).line_items}
    assert items["Testleergut"].tax_class == "B"


def test_rabattaktion_discount_is_separated_from_line_items():
    r = parse_text(FIXTURE)
    assert r.threshold_coupon_discount == Decimal("-0.75")
    # not double-counted as a line item
    assert sum(1 for li in r.line_items if li.name == "K Card XTRA Rabatt") == 1
    # still reconciles: line items + the coupon = the printed total
    assert r.line_item_sum() == r.total
    assert r.totals_match()


def test_rejects_non_kaufland():
    with pytest.raises(ValueError):
        parse_text("Some other shop\nSumme 5,00\nDatum:01.01.26 Zeit: 10:00:00 Bon:1")


def test_image_only_pdf_gets_a_specific_error_not_the_generic_not_kaufland_one():
    # Real case: receipts from before mid-2024 are exported by the Kaufland
    # app as the rendered "Receipt Copy" screen -- every row an image tile,
    # no text layer at all. pypdf returns "" for those, which used to fall
    # through to "does not look like a Kaufland receipt": technically true,
    # but it sends the user hunting for the wrong problem.
    with pytest.raises(ValueError, match="no text layer") as exc:
        parse_text("", label="old.pdf")
    assert "does not look like a Kaufland receipt" not in str(exc.value)
    with pytest.raises(ValueError, match="no text layer"):
        parse_text("   \n\n  ", label="old.pdf")


def test_is_kaufland_when_the_word_only_appears_in_the_footer():
    # Real bug: on a receipt with no "saved ... with Kaufland Card" loyalty
    # line and no "Kaufland - " prefix on the store line (both optional --
    # absent when no card discount applied that trip), the word "Kaufland"
    # doesn't appear until the payment footer ("Kaufland Card XTRA:" /
    # "Kaufland DE <Filiale>"), which a longer item list pushes past any
    # fixed leading-line cutoff. A real 22-item receipt hit this and was
    # wrongly rejected as "not a Kaufland receipt".
    text = "\n".join(
        ["Teststraße 1", "12345 Teststadt", "Tel. 01234/567890", "DE000000000"]
        + [f"  Item {i}       1,00 B" for i in range(20)]
        + ["  Kaufland Card XTRA:          xxxxx0000"]
    )
    assert is_kaufland(text)


# --- retailer dispatch -------------------------------------------------------

REWE_FIXTURE = (Path(__file__).parent / "fixtures" / "rewe_synthetic.txt").read_text("utf-8")


def test_dispatches_rewe_receipts_to_the_rewe_parser():
    r = parse_text(REWE_FIXTURE)
    assert r.store.name == "REWE"
    assert r.receipt_id.startswith("rewe-")
    assert r.totals_match()


def test_kaufland_receipts_still_parse_through_the_dispatcher():
    r = parse_text(FIXTURE)
    assert r.store.name == "Kaufland"
    assert r.receipt_id.startswith("kaufland-")


def test_unrecognised_receipt_names_both_retailers():
    with pytest.raises(ValueError, match="Kaufland or REWE"):
        parse_text("Some other shop\nSumme 5,00\nDatum:01.01.26 Zeit: 10:00:00 Bon:1")


def test_kaufland_errors_carry_the_file_label():
    with pytest.raises(ValueError, match=r"^bad\.pdf: could not find the total"):
        parse_text("Kaufland - Teststraße 1\nDatum:01.01.26 Zeit: 10:00:00 Bon:1", label="bad.pdf")
