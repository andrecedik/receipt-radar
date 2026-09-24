"""Price Integrity Check: is a discounted purchase actually a genuine
reduction, based on the user's own purchase history at that store.

See CONTEXT.md's Price Integrity Check term and
docs/superpowers/specs/2026-08-25-price-integrity-check-design.md.

Genuine price-drop detection only -- not shrinkflation detection. Most
Kaufland-printed item names carry no pack-size information at all, and when
a size IS embedded in a name and it changes, the name string itself changes
(so a shrunk product shows up as a new, historyless item, not a false
"price drop" on the same one) -- there's no reliable signal in this data
source to catch same-name-smaller-pack shrinkflation.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from .models import LineItem, Receipt


class Verdict(BaseModel):
    median_price: Decimal
    current_price: Decimal
    percent_delta: Decimal  # negative = cheaper than usual
    label: str  # "genuine" | "marginal" | "worse_than_usual"
    observation_count: int


def attach_item_discounts(line_items: list[LineItem]) -> list[tuple[int, LineItem, Decimal]]:
    """Pairs each product line (with its index in ``line_items``) with its
    attached per-item discount total (``Decimal(0)`` if none).

    Discount lines are recognised by ``tax_class`` being unset -- discount
    lines never carry a tax class (see parse_pdf.py's ``_DISCOUNT``
    pattern), unlike every product/refund line. Consecutive discount lines
    immediately following one product are all summed into that product's
    attachment -- not observed on real receipts (every sample has exactly
    one discount per item), but not ruled out either, so a second one isn't
    silently dropped.
    """
    pairs: list[tuple[int, LineItem, Decimal]] = []
    i = 0
    while i < len(line_items):
        item = line_items[i]
        if item.tax_class is None:
            # A discount line with nothing preceding it to attach to --
            # skip, it contributes no purchase to attach the discount to.
            i += 1
            continue
        item_index = i
        i += 1
        discount_total = Decimal(0)
        while i < len(line_items) and line_items[i].tax_class is None:
            discount_total += line_items[i].total_price
            i += 1
        pairs.append((item_index, item, discount_total))
    return pairs


def _effective_unit_price(item: LineItem, discount_total: Decimal) -> Decimal:
    """Effective price per unit (or per kg for weight-priced lines) once
    the attached discount is accounted for. ``total_price`` is already the
    real cost and ``quantity`` is already a count or a weight-in-kg
    depending on the line shape, so this one formula covers both cases."""
    return (item.total_price + discount_total) / item.quantity


def compute_verdicts(receipts: list[Receipt]) -> dict[str, dict[int, Verdict]]:
    """receipt_id -> {line_item index -> Verdict}, for every qualifying line
    item (had an attached discount, >=2 prior observations of the same item
    name at the same store) across all receipts.

    Store scoping uses the most specific identifier available
    (``Store.street``, falling back to ``Store.name``), matching
    CONTEXT.md's "at the same store" wording -- distinct from the
    frontend's ``itemPriceHistory()``, which aggregates across all stores
    for a different purpose (a general price-history chart).
    """
    sorted_receipts = sorted(receipts, key=lambda r: r.purchased_at)
    per_receipt_pairs: dict[str, list[tuple[int, LineItem, Decimal]]] = {}
    observations: dict[tuple[str, str], list[tuple[datetime, Decimal]]] = {}

    for r in sorted_receipts:
        store_key = r.store.street or r.store.name
        pairs = attach_item_discounts(r.line_items)
        per_receipt_pairs[r.receipt_id] = pairs
        for _index, item, discount_total in pairs:
            price = _effective_unit_price(item, discount_total)
            key = (item.name, store_key)
            observations.setdefault(key, []).append((r.purchased_at, price))

    verdicts: dict[str, dict[int, Verdict]] = {}
    for r in sorted_receipts:
        store_key = r.store.street or r.store.name
        for index, item, discount_total in per_receipt_pairs[r.receipt_id]:
            if discount_total == 0:
                continue  # no discount on this purchase -- nothing to verify
            key = (item.name, store_key)
            prior = [p for (t, p) in observations[key] if t < r.purchased_at]
            if len(prior) < 2:
                continue
            median_price = statistics.median(prior)
            if median_price == 0:
                continue  # degenerate -- can't compute a meaningful delta
            current_price = _effective_unit_price(item, discount_total)
            percent_delta = (current_price - median_price) / median_price * 100
            if percent_delta <= -5:
                label = "genuine"
            elif percent_delta < 5:
                label = "marginal"
            else:
                label = "worse_than_usual"
            verdicts.setdefault(r.receipt_id, {})[index] = Verdict(
                median_price=median_price, current_price=current_price,
                percent_delta=percent_delta, label=label,
                observation_count=len(prior),
            )
    return verdicts
