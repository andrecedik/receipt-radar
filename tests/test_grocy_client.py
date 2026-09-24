"""Tests for the Grocy REST API wrapper (see CONTEXT.md's Grocy Stock Push
term). No real HTTP -- every test runs against httpx.MockTransport, never
the live https://grocy.example/ instance."""

from decimal import Decimal

import httpx
import pytest

from receipt_radar.grocy_client import GrocyClient


def _client(handler) -> GrocyClient:
    transport = httpx.MockTransport(handler)
    return GrocyClient(base_url="https://grocy.example", api_key="secret-key", transport=transport)


def test_search_products_sends_the_api_key_header_and_parses_results():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["url"] = str(request.url)
        return httpx.Response(200, json=[{"id": 1, "name": "Milch"}, {"id": 2, "name": "Milchreis"}])

    client = _client(handler)
    results = client.search_products("Milch")

    assert captured["headers"]["GROCY-API-KEY"] == "secret-key"
    assert "objects/products" in captured["url"]
    assert [p.id for p in results] == [1, 2]
    assert results[0].name == "Milch"


def test_list_locations():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "name": "Pantry"}])

    client = _client(handler)
    locations = client.list_locations()

    assert len(locations) == 1
    assert locations[0].id == 1
    assert locations[0].name == "Pantry"


def test_list_quantity_units():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 3, "name": "Stück"}])

    client = _client(handler)
    units = client.list_quantity_units()

    assert units[0].id == 3
    assert units[0].name == "Stück"


def test_create_product_returns_the_new_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = request.read()
        return httpx.Response(200, json={"created_object_id": "17"})

    client = _client(handler)
    new_id = client.create_product(name="H-Milch 3,5%", location_id=1, qu_id_purchase=3, qu_id_stock=3)

    assert captured["method"] == "POST"
    assert new_id == 17


def test_add_stock_posts_to_the_purchase_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(200, json={})

    client = _client(handler)
    client.add_stock(product_id=17, amount=Decimal("2"), price=Decimal("1.50"), purchased_date="2026-08-27")

    assert captured["method"] == "POST"
    assert "stock/products/17/add" in captured["url"]


def test_add_stock_raises_on_an_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error_message": "Product not found"})

    client = _client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.add_stock(product_id=999, amount=Decimal("1"), price=Decimal("1.00"), purchased_date="2026-08-27")


def test_check_connection_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"grocy_version": {"Version": "4.0.0"}})

    assert _client(handler).check_connection() is True


def test_check_connection_false_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error_message": "invalid api key"})

    assert _client(handler).check_connection() is False


def test_from_env_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("GROCY_URL", raising=False)
    monkeypatch.delenv("GROCY_API_KEY", raising=False)

    assert GrocyClient.from_env() is None


def test_from_env_builds_a_client_when_configured(monkeypatch):
    monkeypatch.setenv("GROCY_URL", "https://grocy.example")
    monkeypatch.setenv("GROCY_API_KEY", "secret-key")

    client = GrocyClient.from_env()

    assert client is not None
