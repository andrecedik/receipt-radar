"""Tests for local Grocy integration state (see CONTEXT.md's Product Mapping,
Grocy Push Readiness, Grocy Push Attempt, Grocy Product Defaults terms).
Pure filesystem persistence -- no network, no real Grocy instance."""

from datetime import datetime
from decimal import Decimal

from receipt_radar.grocy_store import (
    GrocyProductDefaults,
    GrocyStore,
    LineItemPushState,
    ProductMapping,
    push_readiness,
)
from receipt_radar.models import LineItem, Receipt, Store


def _receipt(line_names: list[str]) -> Receipt:
    line_items = [
        LineItem(name=name, quantity=Decimal(1), unit_price=Decimal("1.00"),
                  total_price=Decimal("1.00"), tax_class="A")
        for name in line_names
    ]
    return Receipt(
        receipt_id="r1", purchased_at=datetime(2026, 8, 27, 10, 0, 0),
        store=Store(name="Kaufland"), line_items=line_items,
        total=Decimal(len(line_names)),
    )


def test_resolve_and_read_back_a_mapping(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.resolve_mapping("H-MILCH 3,5%", grocy_product_id=42)

    mappings = gs.all_mappings()

    assert mappings["H-MILCH 3,5%"] == ProductMapping(
        grocy_product_id=42, skipped=False,
        resolved_at=mappings["H-MILCH 3,5%"].resolved_at,
    )
    assert mappings["H-MILCH 3,5%"].grocy_product_id == 42


def test_resolve_a_skip(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.resolve_mapping("K Card XTRA Rabatt", grocy_product_id=None, skipped=True)

    mapping = gs.all_mappings()["K Card XTRA Rabatt"]

    assert mapping.skipped is True
    assert mapping.grocy_product_id is None


def test_mappings_persist_across_store_instances(tmp_path):
    GrocyStore(data_dir=tmp_path).resolve_mapping("Milch", grocy_product_id=1)

    reloaded = GrocyStore(data_dir=tmp_path)

    assert reloaded.all_mappings()["Milch"].grocy_product_id == 1


def test_push_state_roundtrip(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.set_push_state("r1", 0, LineItemPushState(status="pushed", pushed_at=datetime(2026, 8, 27)))
    gs.set_push_state("r1", 1, LineItemPushState(status="failed", error="connection refused"))

    state = gs.get_push_state("r1")

    assert state[0].status == "pushed"
    assert state[1].status == "failed"
    assert state[1].error == "connection refused"


def test_push_state_is_per_receipt(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.set_push_state("r1", 0, LineItemPushState(status="pushed"))

    assert gs.get_push_state("r2") == {}


def test_defaults_roundtrip(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    assert gs.get_defaults() is None

    gs.set_defaults(GrocyProductDefaults(location_id=3, quantity_unit_id=7))

    assert gs.get_defaults() == GrocyProductDefaults(location_id=3, quantity_unit_id=7)


def test_push_readiness_requires_every_line_item_resolved():
    receipt = _receipt(["Milch", "Brot"])
    mappings = {"Milch": ProductMapping(grocy_product_id=1, resolved_at=datetime(2026, 8, 27))}

    assert push_readiness(receipt, mappings) is False

    mappings["Brot"] = ProductMapping(grocy_product_id=None, skipped=True, resolved_at=datetime(2026, 8, 27))

    assert push_readiness(receipt, mappings) is True


def test_push_readiness_true_for_an_all_skipped_receipt():
    receipt = _receipt(["K Card XTRA Rabatt"])
    mappings = {"K Card XTRA Rabatt": ProductMapping(grocy_product_id=None, skipped=True, resolved_at=datetime(2026, 8, 27))}

    assert push_readiness(receipt, mappings) is True
