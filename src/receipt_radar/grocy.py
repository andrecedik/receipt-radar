"""Grocy Stock Push orchestration -- combines grocy_store.py's local state
with grocy_client.py's API calls (see CONTEXT.md's Grocy Stock Push, Grocy
Push Readiness, and Grocy Push Attempt terms, and
docs/superpowers/specs/2026-08-27-grocy-stock-push-design.md).
"""

from __future__ import annotations

from datetime import datetime

from .grocy_store import GrocyStore, LineItemPushState, push_readiness
from .models import Receipt


def push_receipt(receipt: Receipt, grocy_store: GrocyStore, grocy_client) -> bool:
    """Attempts every line item that isn't already a successful Grocy Push
    Attempt -- safe to call repeatedly (a retry) without double-pushing
    stock for items that already succeeded. Skipped mappings are never
    pushed and never recorded as a push attempt. Returns True iff every
    mapped, non-skipped item ends up "pushed"."""
    mappings = grocy_store.all_mappings()
    push_state = grocy_store.get_push_state(receipt.receipt_id)
    all_ok = True
    for index, li in enumerate(receipt.line_items):
        mapping = mappings.get(li.name)
        if mapping is None or mapping.skipped:
            continue
        existing = push_state.get(index)
        if existing is not None and existing.status == "pushed":
            continue
        try:
            grocy_client.add_stock(
                product_id=mapping.grocy_product_id,
                amount=li.quantity,
                price=li.total_price / li.quantity,
                purchased_date=receipt.purchased_at.date().isoformat(),
            )
            grocy_store.set_push_state(
                receipt.receipt_id, index,
                LineItemPushState(status="pushed", pushed_at=datetime.now()),
            )
        except Exception as exc:
            grocy_store.set_push_state(
                receipt.receipt_id, index,
                LineItemPushState(status="failed", error=str(exc)),
            )
            all_ok = False
    return all_ok


def resolve_mapping_and_push(
    raw_name: str,
    receipts: list[Receipt],
    grocy_store: GrocyStore,
    grocy_client,
    *,
    grocy_product_id: int | None,
    skipped: bool = False,
) -> list[str]:
    """Resolves raw_name once, then pushes every receipt containing it that
    has just reached Grocy Push Readiness as a result -- Product Mapping is
    global, so more than one pending receipt can become ready from a single
    resolution. Returns the ids of receipts a push was attempted for
    (whether it fully succeeded or not -- see GrocyStore.get_push_state for
    per-item outcome)."""
    grocy_store.resolve_mapping(raw_name, grocy_product_id=grocy_product_id, skipped=skipped)
    mappings = grocy_store.all_mappings()
    pushed_receipt_ids = []
    for r in receipts:
        if not any(li.name == raw_name for li in r.line_items):
            continue
        if not push_readiness(r, mappings):
            continue
        push_receipt(r, grocy_store, grocy_client)
        pushed_receipt_ids.append(r.receipt_id)
    return pushed_receipt_ids


def create_product_and_map_and_push(
    raw_name: str,
    new_product_name: str,
    receipts: list[Receipt],
    grocy_store: GrocyStore,
    grocy_client,
) -> list[str]:
    """The picker's "create new" path: creates the product in Grocy using
    Grocy Product Defaults, then behaves exactly like
    resolve_mapping_and_push with the newly created product id."""
    defaults = grocy_store.get_defaults()
    if defaults is None:
        raise ValueError("Grocy Product Defaults are not configured yet.")
    product_id = grocy_client.create_product(
        name=new_product_name,
        location_id=defaults.location_id,
        qu_id_purchase=defaults.quantity_unit_id,
        qu_id_stock=defaults.quantity_unit_id,
    )
    return resolve_mapping_and_push(
        raw_name, receipts, grocy_store, grocy_client, grocy_product_id=product_id,
    )
