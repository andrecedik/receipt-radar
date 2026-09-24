"""Tests for the Web Upload HTTP endpoint (see CONTEXT.md's Web Upload)."""

import json
from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from receipt_radar.grocy_client import GrocyLocation, GrocyProduct, GrocyQuantityUnit
from receipt_radar.grocy_store import GrocyProductDefaults, GrocyStore
from receipt_radar.models import LineItem, Receipt, Store
from receipt_radar.server import create_app
from receipt_radar.store import ReceiptStore


def _client(tmp_path):
    store = ReceiptStore(data_dir=tmp_path / "data")
    app = create_app(store, web_dir=tmp_path / "web")
    return TestClient(app), store


def test_health_check(tmp_path):
    client, _store = _client(tmp_path)

    res = client.get("/api/health")

    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def _install_fake_parse_pdf(monkeypatch, *, receipt_id="r1", total="12.34", raises=None):
    """Stand in for the real parse_pdf: mirrors its contract of setting
    source_file to the path it was given, without touching a real PDF."""

    def fake_parse_pdf(path):
        if raises is not None:
            raise raises
        return Receipt(
            receipt_id=receipt_id,
            purchased_at=datetime(2026, 8, 21, 23, 39, 39),
            store=Store(name="Kaufland"),
            line_items=[],
            total=Decimal(total),
            source_file=str(path),
        )

    monkeypatch.setattr("receipt_radar.server.parse_pdf", fake_parse_pdf)


def test_upload_adds_a_new_receipt(tmp_path, monkeypatch):
    client, store = _client(tmp_path)
    _install_fake_parse_pdf(monkeypatch, receipt_id="r1", total="12.34")

    res = client.post(
        "/api/upload", files={"file": ("receipt.pdf", b"fake pdf bytes", "application/pdf")}
    )

    assert res.status_code == 200
    assert res.json() == {"status": "added", "receipt_id": "r1", "total": "12.34", "currency": "EUR"}
    assert len(store.all()) == 1


def test_upload_is_idempotent(tmp_path, monkeypatch):
    client, store = _client(tmp_path)
    _install_fake_parse_pdf(monkeypatch, receipt_id="r1", total="12.34")
    client.post("/api/upload", files={"file": ("receipt.pdf", b"fake pdf bytes", "application/pdf")})

    res = client.post(
        "/api/upload", files={"file": ("receipt.pdf", b"fake pdf bytes 2", "application/pdf")}
    )

    assert res.status_code == 200
    assert res.json() == {"status": "duplicate", "receipt_id": "r1"}
    assert len(store.all()) == 1


def test_upload_rejects_a_non_pdf(tmp_path):
    client, store = _client(tmp_path)

    res = client.post(
        "/api/upload", files={"file": ("receipt.txt", b"not a pdf", "text/plain")}
    )

    assert res.status_code == 422
    assert store.all() == []


def test_upload_reports_a_parse_failure(tmp_path, monkeypatch):
    client, store = _client(tmp_path)
    _install_fake_parse_pdf(monkeypatch, raises=ValueError("not a Kaufland receipt"))

    res = client.post(
        "/api/upload", files={"file": ("receipt.pdf", b"fake pdf bytes", "application/pdf")}
    )

    assert res.status_code == 422
    assert res.json() == {"detail": "not a Kaufland receipt"}
    assert store.all() == []


def test_upload_refreshes_the_web_data(tmp_path, monkeypatch):
    client, _store = _client(tmp_path)
    web_dir = tmp_path / "web"
    _install_fake_parse_pdf(monkeypatch, receipt_id="r1", total="12.34")

    client.post("/api/upload", files={"file": ("receipt.pdf", b"fake pdf bytes", "application/pdf")})

    receipts_json = web_dir / "public" / "data" / "receipts.json"
    assert receipts_json.exists()
    data = json.loads(receipts_json.read_text("utf-8"))
    assert len(data) == 1
    assert data[0]["receipt_id"] == "r1"

    # source_file (see _install_fake_parse_pdf) points at the real bytes the
    # endpoint persisted under store.data_dir/uploads/ before parsing, so
    # export_web_data finds a real file to copy here -- not a fake.
    assert (web_dir / "public" / "pdfs" / "r1.pdf").exists()


def test_static_mount_serves_index_html(tmp_path):
    web_dir = tmp_path / "web"
    (web_dir / "public").mkdir(parents=True)
    (web_dir / "public" / "index.html").write_text("<h1>receipt-radar</h1>", encoding="utf-8")
    store = ReceiptStore(data_dir=tmp_path / "data")
    client = TestClient(create_app(store, web_dir))

    res = client.get("/")

    assert res.status_code == 200
    assert "<h1>receipt-radar</h1>" in res.text


def test_static_mount_serves_nested_assets(tmp_path):
    web_dir = tmp_path / "web"
    (web_dir / "public" / "assets").mkdir(parents=True)
    (web_dir / "public" / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    store = ReceiptStore(data_dir=tmp_path / "data")
    client = TestClient(create_app(store, web_dir))

    res = client.get("/assets/app.js")

    assert res.status_code == 200
    assert res.text == "console.log('hi')"


def test_static_mount_handles_missing_public_dir(tmp_path):
    web_dir = tmp_path / "web"  # web_dir/public/ deliberately never created

    client, _store = _client(tmp_path)

    res = client.get("/")

    assert res.status_code == 404


class _FakeGrocyClient:
    def __init__(self, *, connected=True, fail_product_ids=None):
        self.connected = connected
        self.added = []
        self.created = []
        self._fail_product_ids = fail_product_ids or set()
        self._next_id = 200

    def search_products(self, query):
        return [GrocyProduct(id=1, name="Milch")] if query else []

    def list_locations(self):
        return [GrocyLocation(id=1, name="Pantry")]

    def list_quantity_units(self):
        return [GrocyQuantityUnit(id=3, name="Stück")]

    def create_product(self, name, location_id, qu_id_purchase, qu_id_stock):
        self.created.append(name)
        self._next_id += 1
        return self._next_id

    def add_stock(self, product_id, amount, price, purchased_date):
        if product_id in self._fail_product_ids:
            raise RuntimeError("Grocy unreachable")
        self.added.append(product_id)

    def check_connection(self):
        return self.connected


def _grocy_client_app(tmp_path, *, grocy_client=None):
    store = ReceiptStore(data_dir=tmp_path / "data")
    grocy_store = GrocyStore(data_dir=tmp_path / "data")
    app = create_app(store, web_dir=tmp_path / "web", grocy_store=grocy_store, grocy_client=grocy_client)
    return TestClient(app), store, grocy_store


def _receipt_with_one_item(rid="r1", name="Milch") -> Receipt:
    li = LineItem(name=name, quantity=Decimal(1), unit_price=Decimal("2.00"),
                  total_price=Decimal("2.00"), tax_class="A")
    return Receipt(receipt_id=rid, purchased_at=datetime(2026, 8, 27, 10, 0, 0),
                    store=Store(name="Kaufland"), line_items=[li], total=Decimal("2.00"))


def test_grocy_endpoints_503_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("GROCY_URL", raising=False)
    monkeypatch.delenv("GROCY_API_KEY", raising=False)
    client, store, _gs = _grocy_client_app(tmp_path)
    store.save(_receipt_with_one_item())

    assert client.get("/api/grocy/search", params={"q": "Milch"}).status_code == 503
    assert client.post("/api/grocy/mappings", json={"raw_name": "Milch", "grocy_product_id": 1}).status_code == 503
    assert client.post("/api/grocy/receipts/r1/retry").status_code == 503


def test_grocy_pending_lists_receipts_with_unresolved_items(tmp_path):
    client, store, _gs = _grocy_client_app(tmp_path, grocy_client=_FakeGrocyClient())
    store.save(_receipt_with_one_item("r1", "Milch"))

    res = client.get("/api/grocy/pending")

    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["receipt_id"] == "r1"
    assert body[0]["unresolved"] == [{"index": 0, "name": "Milch"}]
    assert body[0]["failed"] == []


def test_grocy_pending_omits_a_fully_resolved_receipt(tmp_path):
    client, store, gs = _grocy_client_app(tmp_path, grocy_client=_FakeGrocyClient())
    store.save(_receipt_with_one_item("r1", "Milch"))
    gs.resolve_mapping("Milch", grocy_product_id=1)

    assert client.get("/api/grocy/pending").json() == []


def test_grocy_search_proxies_to_the_client(tmp_path):
    client, _store, _gs = _grocy_client_app(tmp_path, grocy_client=_FakeGrocyClient())

    res = client.get("/api/grocy/search", params={"q": "Milch"})

    assert res.status_code == 200
    assert res.json() == [{"id": 1, "name": "Milch"}]


def test_grocy_resolve_mapping_pushes_the_ready_receipt(tmp_path):
    fake = _FakeGrocyClient()
    client, store, gs = _grocy_client_app(tmp_path, grocy_client=fake)
    store.save(_receipt_with_one_item("r1", "Milch"))

    res = client.post("/api/grocy/mappings", json={"raw_name": "Milch", "grocy_product_id": 1})

    assert res.status_code == 200
    assert res.json() == {"pushed_receipt_ids": ["r1"]}
    assert gs.get_push_state("r1")[0].status == "pushed"


def test_grocy_resolve_mapping_pushes_every_receipt_that_becomes_ready(tmp_path):
    # Product Mapping is global (keyed by raw name, not receipt) -- one
    # resolution over the HTTP endpoint can bring multiple pending receipts
    # to Grocy Push Readiness at once. grocy.py's resolve_mapping_and_push
    # already covers this at the unit level (see
    # test_resolve_mapping_and_push_pushes_every_receipt_that_becomes_ready
    # in tests/test_grocy.py); this proves the real /api/grocy/mappings
    # route wires it through correctly too.
    fake = _FakeGrocyClient()
    client, store, gs = _grocy_client_app(tmp_path, grocy_client=fake)
    store.save(_receipt_with_one_item("r1", "Milch"))
    store.save(_receipt_with_one_item("r2", "Milch"))

    res = client.post("/api/grocy/mappings", json={"raw_name": "Milch", "grocy_product_id": 1})

    assert res.status_code == 200
    assert set(res.json()["pushed_receipt_ids"]) == {"r1", "r2"}
    assert gs.get_push_state("r1")[0].status == "pushed"
    assert gs.get_push_state("r2")[0].status == "pushed"


def test_grocy_resolve_mapping_with_a_new_product_name_creates_it(tmp_path):
    fake = _FakeGrocyClient()
    client, store, gs = _grocy_client_app(tmp_path, grocy_client=fake)
    gs.set_defaults(GrocyProductDefaults(location_id=1, quantity_unit_id=3))
    store.save(_receipt_with_one_item("r1", "Neues Produkt"))

    res = client.post("/api/grocy/mappings", json={"raw_name": "Neues Produkt", "new_product_name": "Neues Produkt"})

    assert res.status_code == 200
    assert fake.created == ["Neues Produkt"]
    assert res.json()["pushed_receipt_ids"] == ["r1"]


def test_grocy_resolve_mapping_with_a_new_product_name_409s_without_defaults(tmp_path):
    fake = _FakeGrocyClient()
    client, store, _gs = _grocy_client_app(tmp_path, grocy_client=fake)
    store.save(_receipt_with_one_item("r1", "Neues Produkt"))

    res = client.post("/api/grocy/mappings", json={"raw_name": "Neues Produkt", "new_product_name": "Neues Produkt"})

    assert res.status_code == 409
    assert res.json()["detail"] == "Grocy Product Defaults are not configured yet."
    assert fake.created == []


def test_grocy_resolve_mapping_skip(tmp_path):
    client, store, gs = _grocy_client_app(tmp_path, grocy_client=_FakeGrocyClient())
    store.save(_receipt_with_one_item("r1", "K Card XTRA Rabatt"))

    res = client.post("/api/grocy/mappings", json={"raw_name": "K Card XTRA Rabatt", "skipped": True})

    assert res.status_code == 200
    assert res.json() == {"pushed_receipt_ids": ["r1"]}
    assert gs.all_mappings()["K Card XTRA Rabatt"].skipped is True


def test_grocy_retry_only_reattempts_failed_items(tmp_path):
    fake = _FakeGrocyClient(fail_product_ids={1})
    client, store, gs = _grocy_client_app(tmp_path, grocy_client=fake)
    store.save(_receipt_with_one_item("r1", "Milch"))
    client.post("/api/grocy/mappings", json={"raw_name": "Milch", "grocy_product_id": 1})
    assert gs.get_push_state("r1")[0].status == "failed"

    fake._fail_product_ids = set()  # Grocy reachable again
    res = client.post("/api/grocy/receipts/r1/retry")

    assert res.status_code == 200
    assert res.json() == {"all_pushed": True}
    assert gs.get_push_state("r1")[0].status == "pushed"


def test_grocy_retry_409s_for_a_receipt_that_is_not_fully_mapped(tmp_path):
    # grocy_retry must re-check Grocy Push Readiness itself -- the
    # all-or-nothing design means a receipt should never get a partial push,
    # even via a direct retry call that bypasses the normal
    # /api/grocy/mappings flow.
    fake = _FakeGrocyClient()
    client, store, gs = _grocy_client_app(tmp_path, grocy_client=fake)
    li_mapped = LineItem(name="Milch", quantity=Decimal(1), unit_price=Decimal("2.00"),
                          total_price=Decimal("2.00"), tax_class="A")
    li_unmapped = LineItem(name="Butter", quantity=Decimal(1), unit_price=Decimal("2.00"),
                            total_price=Decimal("2.00"), tax_class="A")
    receipt = Receipt(receipt_id="r1", purchased_at=datetime(2026, 8, 27, 10, 0, 0),
                       store=Store(name="Kaufland"), line_items=[li_mapped, li_unmapped],
                       total=Decimal("4.00"))
    store.save(receipt)
    gs.resolve_mapping("Milch", grocy_product_id=1)  # "Butter" still unresolved

    res = client.post("/api/grocy/receipts/r1/retry")

    assert res.status_code == 409
    assert fake.added == []
    assert gs.get_push_state("r1") == {}


def test_grocy_retry_404s_for_an_unknown_receipt(tmp_path):
    client, _store, _gs = _grocy_client_app(tmp_path, grocy_client=_FakeGrocyClient())

    assert client.post("/api/grocy/receipts/does-not-exist/retry").status_code == 404


def test_grocy_settings_get_reports_connection_and_choices(tmp_path):
    client, _store, _gs = _grocy_client_app(tmp_path, grocy_client=_FakeGrocyClient(connected=True))

    res = client.get("/api/grocy/settings")

    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is True
    assert body["defaults"] is None
    assert body["locations"] == [{"id": 1, "name": "Pantry"}]
    assert body["quantity_units"] == [{"id": 3, "name": "Stück"}]


def test_grocy_settings_put_persists_defaults(tmp_path):
    client, _store, gs = _grocy_client_app(tmp_path, grocy_client=_FakeGrocyClient())

    res = client.put("/api/grocy/settings", json={"location_id": 1, "quantity_unit_id": 3})

    assert res.status_code == 200
    assert gs.get_defaults() == GrocyProductDefaults(location_id=1, quantity_unit_id=3)
