"""Local, offline state for Grocy Stock Push (see CONTEXT.md's Product
Mapping, Grocy Push Readiness, Grocy Push Attempt, Grocy Product Defaults
terms and docs/superpowers/specs/2026-08-27-grocy-stock-push-design.md).

No network calls here -- this only persists our own local decisions and
computes readiness from them. See grocy_client.py for the Grocy API wrapper
and grocy.py for the orchestration that ties the two together.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from .models import Receipt
from .store import default_data_dir


class ProductMapping(BaseModel):
    """A resolved Product Mapping: either a Grocy product id, or a skip."""
    grocy_product_id: int | None
    skipped: bool = False
    resolved_at: datetime


class LineItemPushState(BaseModel):
    """A Grocy Push Attempt outcome for one line item on one receipt."""
    status: str  # "pushed" | "failed"
    error: str | None = None
    pushed_at: datetime | None = None


class GrocyProductDefaults(BaseModel):
    """Grocy Product Defaults -- applied to every product created via the
    Product Mapping picker's "create new" path."""
    location_id: int
    quantity_unit_id: int


def _safe_filename(receipt_id: str) -> str:
    # Same sanitization as ReceiptStore._path_for -- receipt_id is used as a
    # filename, keep it filesystem-safe.
    return receipt_id.replace("/", "_").replace(os.sep, "_") + ".json"


class GrocyStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.grocy_dir = self.data_dir / "grocy"
        self.mappings_path = self.grocy_dir / "mappings.json"
        self.push_state_dir = self.grocy_dir / "push_state"
        self.settings_path = self.grocy_dir / "settings.json"
        self.grocy_dir.mkdir(parents=True, exist_ok=True)
        self.push_state_dir.mkdir(parents=True, exist_ok=True)

    # -- Product Mapping ---------------------------------------------------

    def all_mappings(self) -> dict[str, ProductMapping]:
        if not self.mappings_path.exists():
            return {}
        raw = json.loads(self.mappings_path.read_text("utf-8"))
        return {name: ProductMapping.model_validate(m) for name, m in raw.items()}

    def resolve_mapping(
        self, raw_name: str, *, grocy_product_id: int | None, skipped: bool = False
    ) -> ProductMapping:
        mappings = self.all_mappings()
        mapping = ProductMapping(
            grocy_product_id=grocy_product_id, skipped=skipped, resolved_at=datetime.now()
        )
        mappings[raw_name] = mapping
        self.mappings_path.write_text(
            json.dumps({name: m.model_dump(mode="json") for name, m in mappings.items()}, indent=2),
            encoding="utf-8",
        )
        return mapping

    # -- Grocy Push Attempt --------------------------------------------------

    def get_push_state(self, receipt_id: str) -> dict[int, LineItemPushState]:
        path = self.push_state_dir / _safe_filename(receipt_id)
        if not path.exists():
            return {}
        raw = json.loads(path.read_text("utf-8"))
        return {int(index): LineItemPushState.model_validate(s) for index, s in raw.items()}

    def set_push_state(self, receipt_id: str, index: int, state: LineItemPushState) -> None:
        path = self.push_state_dir / _safe_filename(receipt_id)
        current = self.get_push_state(receipt_id)
        current[index] = state
        path.write_text(
            json.dumps({str(i): s.model_dump(mode="json") for i, s in current.items()}, indent=2),
            encoding="utf-8",
        )

    # -- Grocy Product Defaults ----------------------------------------------

    def get_defaults(self) -> GrocyProductDefaults | None:
        if not self.settings_path.exists():
            return None
        return GrocyProductDefaults.model_validate_json(self.settings_path.read_text("utf-8"))

    def set_defaults(self, defaults: GrocyProductDefaults) -> None:
        self.settings_path.write_text(defaults.model_dump_json(indent=2), encoding="utf-8")


def push_readiness(receipt: Receipt, mappings: dict[str, ProductMapping]) -> bool:
    """Grocy Push Readiness: every line item on the receipt has a resolved
    Product Mapping (matched or skipped) -- see CONTEXT.md."""
    return all(li.name in mappings for li in receipt.line_items)
