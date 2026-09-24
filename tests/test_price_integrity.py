"""Tests for Price Integrity Check (see CONTEXT.md's Price Integrity Check
term and docs/superpowers/specs/2026-08-25-price-integrity-check-design.md).
No PDF/text parsing involved -- these operate on Receipt/LineItem objects
directly, so nothing here depends on samples/ (gitignored real receipts)."""

from datetime import datetime
from decimal import Decimal

from receipt_radar.models import LineItem, Receipt, Store
from receipt_radar.price_integrity import attach_item_discounts, compute_verdicts


def _product(name="Milch", total_price="2.00", quantity="1", tax_class="B") -> LineItem:
    return LineItem(
        name=name, quantity=Decimal(quantity), unit_price=Decimal(total_price) / Decimal(quantity),
        total_price=Decimal(total_price), tax_class=tax_class,
    )


def _discount(name="K Card XTRA Rabatt", amount="-0.30") -> LineItem:
    return LineItem(name=name, total_price=Decimal(amount))


def _receipt(rid: str, when: str, line_items: list[LineItem], store_street="Teststraße 1") -> Receipt:
    total = sum((li.total_price for li in line_items), Decimal(0))
    return Receipt(
        receipt_id=rid, purchased_at=datetime.fromisoformat(when),
        store=Store(name="Kaufland", street=store_street),
        line_items=line_items, total=total,
    )


def test_attach_item_discounts_pairs_each_item_with_its_following_discount():
    a = _product(name="A", total_price="2.00")
    disc = _discount(amount="-0.50")
    b = _product(name="B", total_price="1.00")
    pairs = attach_item_discounts([a, disc, b])

    assert pairs == [(0, a, Decimal("-0.50")), (2, b, Decimal("0"))]


def test_attach_item_discounts_sums_consecutive_discounts_on_one_item():
    a = _product(name="A", total_price="2.00")
    disc1 = _discount(name="Mengenrabatt", amount="-0.20")
    disc2 = _discount(name="K Card XTRA Rabatt", amount="-0.30")
    pairs = attach_item_discounts([a, disc1, disc2])

    assert pairs == [(0, a, Decimal("-0.50"))]


def test_compute_verdicts_requires_two_prior_observations():
    r1 = _receipt("r1", "2026-01-01T10:00:00", [_product(total_price="2.00")])
    r2 = _receipt("r2", "2026-01-08T10:00:00", [_product(total_price="1.70"), _discount()])

    verdicts = compute_verdicts([r1, r2])

    assert verdicts == {}  # only 1 prior observation for r2's discounted purchase


def test_compute_verdicts_labels_a_clear_price_drop_genuine():
    r1 = _receipt("r1", "2026-01-01T10:00:00", [_product(total_price="2.00")])
    r2 = _receipt("r2", "2026-01-08T10:00:00", [_product(total_price="2.00")])
    # product's own printed total_price is 2.00 (matching r1/r2); the
    # attached -0.30 discount brings the effective price to 1.70 (-15%)
    r3 = _receipt("r3", "2026-01-15T10:00:00", [_product(total_price="2.00"), _discount(amount="-0.30")])

    verdicts = compute_verdicts([r1, r2, r3])

    v = verdicts["r3"][0]
    assert v.median_price == Decimal("2.00")
    assert v.current_price == Decimal("1.70")
    assert v.percent_delta == Decimal("-15.0")
    assert v.label == "genuine"
    assert v.observation_count == 2


def test_compute_verdicts_labels_a_small_difference_marginal():
    r1 = _receipt("r1", "2026-01-01T10:00:00", [_product(total_price="2.00")])
    r2 = _receipt("r2", "2026-01-08T10:00:00", [_product(total_price="2.00")])
    # printed total_price 2.00, attached -0.05 discount -> effective 1.95
    # (-2.5%, strictly between the -5/+5 label boundaries)
    r3 = _receipt("r3", "2026-01-15T10:00:00", [_product(total_price="2.00"), _discount(amount="-0.05")])

    verdicts = compute_verdicts([r1, r2, r3])

    assert verdicts["r3"][0].label == "marginal"


def test_compute_verdicts_labels_a_price_increase_worse_than_usual():
    r1 = _receipt("r1", "2026-01-01T10:00:00", [_product(total_price="2.00")])
    r2 = _receipt("r2", "2026-01-08T10:00:00", [_product(total_price="2.00")])
    # still has a discount attached, but the price is still higher than usual
    r3 = _receipt("r3", "2026-01-15T10:00:00", [_product(total_price="2.20"), _discount(amount="-0.05")])

    verdicts = compute_verdicts([r1, r2, r3])

    assert verdicts["r3"][0].label == "worse_than_usual"


def test_compute_verdicts_scopes_by_store():
    # Two prior purchases at a DIFFERENT store must not count toward this
    # store's median.
    other_store = _receipt("o1", "2026-01-01T10:00:00", [_product(total_price="1.00")], store_street="Elsewhere 1")
    other_store2 = _receipt("o2", "2026-01-02T10:00:00", [_product(total_price="1.00")], store_street="Elsewhere 1")
    r1 = _receipt("r1", "2026-01-08T10:00:00", [_product(total_price="2.00")])
    r2 = _receipt("r2", "2026-01-15T10:00:00", [_product(total_price="1.70"), _discount(amount="-0.30")])

    verdicts = compute_verdicts([other_store, other_store2, r1, r2])

    assert verdicts == {}  # only r1 is prior history at r2's store -- still just 1 observation


def test_compute_verdicts_skips_purchases_with_no_discount():
    r1 = _receipt("r1", "2026-01-01T10:00:00", [_product(total_price="2.00")])
    r2 = _receipt("r2", "2026-01-08T10:00:00", [_product(total_price="2.00")])
    r3 = _receipt("r3", "2026-01-15T10:00:00", [_product(total_price="2.00")])  # no discount

    verdicts = compute_verdicts([r1, r2, r3])

    assert verdicts == {}
