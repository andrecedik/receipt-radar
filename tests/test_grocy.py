"""Tests for Grocy push orchestration (see CONTEXT.md's Grocy Push
Readiness and Grocy Push Attempt terms). Uses a hand-written fake
GrocyClient -- never the real one, never a live Grocy instance."""

from datetime import datetime
from decimal import Decimal

from receipt_radar.grocy import (
    create_product_and_map_and_push,
    push_receipt,
    resolve_mapping_and_push,
)
from receipt_radar.grocy_store import GrocyProductDefaults, GrocyStore
from receipt_radar.models import LineItem, Receipt, Store


class _FakeGrocyClient:
    """Records every add_stock call; can be told to fail on specific
    product ids to simulate a partial-failure push."""

    def __init__(self, fail_product_ids: set[int] | None = None):
        self.added: list[tuple[int, Decimal, Decimal, str]] = []
        self.created: list[dict] = []
        self._fail_product_ids = fail_product_ids or set()
        self._next_created_id = 100

    def add_stock(self, product_id, amount, price, purchased_date):
        if product_id in self._fail_product_ids:
            raise RuntimeError(f"Grocy rejected product {product_id}")
        self.added.append((product_id, amount, price, purchased_date))

    def create_product(self, name, location_id, qu_id_purchase, qu_id_stock):
        self.created.append({
            "name": name, "location_id": location_id,
            "qu_id_purchase": qu_id_purchase, "qu_id_stock": qu_id_stock,
        })
        self._next_created_id += 1
        return self._next_created_id


def _line(name, total_price="2.00", quantity="1") -> LineItem:
    return LineItem(
        name=name, quantity=Decimal(quantity), unit_price=Decimal(total_price),
        total_price=Decimal(total_price), tax_class="A",
    )


def _receipt(rid, line_items) -> Receipt:
    total = sum((li.total_price for li in line_items), Decimal(0))
    return Receipt(
        receipt_id=rid, purchased_at=datetime(2026, 8, 27, 10, 0, 0),
        store=Store(name="Kaufland"), line_items=line_items, total=total,
    )


def test_push_receipt_pushes_every_mapped_item(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.resolve_mapping("Milch", grocy_product_id=1)
    gs.resolve_mapping("Brot", grocy_product_id=2)
    receipt = _receipt("r1", [_line("Milch", total_price="2.00"), _line("Brot", total_price="1.50")])
    client = _FakeGrocyClient()

    ok = push_receipt(receipt, gs, client)

    assert ok is True
    # both lines have quantity=1 (the _line() default), so amount == price
    # numerically here -- see the dedicated quantity-vs-price test below for
    # a case where they're forced to differ, catching an amount/price swap.
    assert (1, Decimal("1"), Decimal("2.00"), "2026-08-27") in client.added
    assert (2, Decimal("1"), Decimal("1.50"), "2026-08-27") in client.added
    state = gs.get_push_state("r1")
    assert state[0].status == "pushed"
    assert state[1].status == "pushed"


def test_push_receipt_pushes_the_receipts_quantity_as_the_amount(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.resolve_mapping("Milch", grocy_product_id=1)
    receipt = _receipt("r1", [_line("Milch", total_price="3.00", quantity="2")])
    client = _FakeGrocyClient()

    push_receipt(receipt, gs, client)

    # amount is the raw receipt quantity (2); price is per-unit (3.00 / 2 =
    # 1.50) -- these must never be swapped (see CONTEXT.md: no unit
    # conversion, quantity pushed as-is).
    assert client.added == [(1, Decimal("2"), Decimal("1.50"), "2026-08-27")]


def test_push_receipt_skips_skipped_items(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.resolve_mapping("K Card XTRA Rabatt", grocy_product_id=None, skipped=True)
    receipt = _receipt("r1", [_line("K Card XTRA Rabatt", total_price="-0.30")])
    client = _FakeGrocyClient()

    ok = push_receipt(receipt, gs, client)

    assert ok is True
    assert client.added == []
    assert gs.get_push_state("r1") == {}


def test_push_receipt_records_failure_and_does_not_double_push_on_retry(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.resolve_mapping("Milch", grocy_product_id=1)
    gs.resolve_mapping("Brot", grocy_product_id=2)
    receipt = _receipt("r1", [_line("Milch"), _line("Brot")])
    client = _FakeGrocyClient(fail_product_ids={2})

    first = push_receipt(receipt, gs, client)
    assert first is False
    assert gs.get_push_state("r1")[0].status == "pushed"
    assert gs.get_push_state("r1")[1].status == "failed"

    client.added.clear()  # simulate Grocy now being reachable again
    second_client = _FakeGrocyClient()
    second = push_receipt(receipt, gs, second_client)

    assert second is True
    # only the previously-failed item (Brot, index 1) is retried -- Milch
    # (index 0) already succeeded and must not be pushed twice. Both lines
    # have quantity=1 (the _line() default), so amount == price == 2.00.
    assert second_client.added == [(2, Decimal("1"), Decimal("2.00"), "2026-08-27")]


def test_resolve_mapping_and_push_pushes_the_single_ready_receipt(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    receipt = _receipt("r1", [_line("Milch")])
    client = _FakeGrocyClient()

    pushed = resolve_mapping_and_push("Milch", [receipt], gs, client, grocy_product_id=1)

    assert pushed == ["r1"]
    assert client.added == [(1, Decimal("1"), Decimal("2.00"), "2026-08-27")]


def test_resolve_mapping_and_push_pushes_every_receipt_that_becomes_ready(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.resolve_mapping("Brot", grocy_product_id=2)  # already resolved on both receipts
    r1 = _receipt("r1", [_line("Milch"), _line("Brot")])
    r2 = _receipt("r2", [_line("Milch")])
    r3 = _receipt("r3", [_line("Milch"), _line("Käse")])  # Käse still unresolved -- not ready
    client = _FakeGrocyClient()

    pushed = resolve_mapping_and_push("Milch", [r1, r2, r3], gs, client, grocy_product_id=1)

    assert set(pushed) == {"r1", "r2"}


def test_resolve_mapping_and_push_does_not_push_a_receipt_still_missing_other_mappings(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    receipt = _receipt("r1", [_line("Milch"), _line("Käse")])
    client = _FakeGrocyClient()

    pushed = resolve_mapping_and_push("Milch", [receipt], gs, client, grocy_product_id=1)

    assert pushed == []
    assert client.added == []


def test_create_product_and_map_and_push_uses_defaults(tmp_path):
    gs = GrocyStore(data_dir=tmp_path)
    gs.set_defaults(GrocyProductDefaults(location_id=1, quantity_unit_id=3))
    receipt = _receipt("r1", [_line("Neues Produkt")])
    client = _FakeGrocyClient()

    pushed = create_product_and_map_and_push("Neues Produkt", "Neues Produkt", [receipt], gs, client)

    assert pushed == ["r1"]
    assert client.created == [{
        "name": "Neues Produkt", "location_id": 1, "qu_id_purchase": 3, "qu_id_stock": 3,
    }]
    assert gs.all_mappings()["Neues Produkt"].grocy_product_id == 101
