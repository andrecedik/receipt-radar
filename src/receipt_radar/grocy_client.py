"""Thin wrapper over Grocy's REST API (see CONTEXT.md's Grocy Stock Push
term and docs/superpowers/specs/2026-08-27-grocy-stock-push-design.md).

No local state or persistence here -- see grocy_store.py for that, and
grocy.py for the orchestration that combines the two. Auth is a
GROCY-API-KEY header, per Grocy's documented API convention.

The filter syntax used in search_products() and the required fields in
create_product() are recalled from Grocy's general API shape, not
confirmed against a live response -- verify both against the real instance
(https://grocy.example/api/ serves interactive API docs) and adjust if
they don't match.
"""

from __future__ import annotations

import os
from decimal import Decimal

import httpx
from pydantic import BaseModel


class GrocyProduct(BaseModel):
    id: int
    name: str


class GrocyLocation(BaseModel):
    id: int
    name: str


class GrocyQuantityUnit(BaseModel):
    id: int
    name: str


class GrocyClient:
    def __init__(self, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/",
            headers={"GROCY-API-KEY": api_key},
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> "GrocyClient | None":
        base_url = os.environ.get("GROCY_URL")
        api_key = os.environ.get("GROCY_API_KEY")
        if not base_url or not api_key:
            return None
        return cls(base_url=base_url, api_key=api_key)

    def search_products(self, query: str) -> list[GrocyProduct]:
        resp = self._client.get("objects/products", params={"query[]": f"name~{query}"})
        resp.raise_for_status()
        return [GrocyProduct(id=p["id"], name=p["name"]) for p in resp.json()]

    def list_locations(self) -> list[GrocyLocation]:
        resp = self._client.get("objects/locations")
        resp.raise_for_status()
        return [GrocyLocation(id=loc["id"], name=loc["name"]) for loc in resp.json()]

    def list_quantity_units(self) -> list[GrocyQuantityUnit]:
        resp = self._client.get("objects/quantity_units")
        resp.raise_for_status()
        return [GrocyQuantityUnit(id=q["id"], name=q["name"]) for q in resp.json()]

    def create_product(self, name: str, location_id: int, qu_id_purchase: int, qu_id_stock: int) -> int:
        resp = self._client.post(
            "objects/products",
            json={
                "name": name,
                "location_id": location_id,
                "qu_id_purchase": qu_id_purchase,
                "qu_id_stock": qu_id_stock,
            },
        )
        resp.raise_for_status()
        return int(resp.json()["created_object_id"])

    def add_stock(self, product_id: int, amount: Decimal, price: Decimal, purchased_date: str) -> None:
        # Decimal isn't JSON-serializable via httpx's json= encoder -- convert
        # to float at this one boundary (see Global Constraints in the plan).
        resp = self._client.post(
            f"stock/products/{product_id}/add",
            json={
                "amount": float(amount),
                "transaction_type": "purchase",
                "price": float(price),
                "purchased_date": purchased_date,
            },
        )
        resp.raise_for_status()

    def check_connection(self) -> bool:
        try:
            resp = self._client.get("system/info")
            resp.raise_for_status()
            return True
        except httpx.HTTPError:
            return False
