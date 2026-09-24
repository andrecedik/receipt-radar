"""Canonical receipt data model.

This is the single shared shape that every ingestion path emits into:

* the PDF pipeline (``parse_pdf.py``) — the route in use today, and
* a future auto-sync API client (see ``docs/api.md``) — not yet built.

Keeping both paths on the same model means everything downstream (spending
rollups, price history, me-brain export, Home Assistant) only ever has to
understand one thing.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    """A single article line on a receipt."""

    name: str
    quantity: Decimal = Decimal(1)
    unit_price: Decimal | None = None  # price per unit, if the receipt shows it
    total_price: Decimal  # what this line actually cost (unit_price * quantity)
    tax_class: str | None = None  # "A" (19%) or "B" (7%); None marks a discount line
    article_number: str | None = None  # stable key for price history, if available

    # Pack size / purchased weight, e.g. "750g" parsed out of the name, or the
    # actual weight for a per-kg-priced item (same number as `quantity` there,
    # kept explicit so a consumer never has to guess whether `quantity` is a
    # count or a weight). `size_unit` is one of "g", "kg", "ml", "l".
    size_value: Decimal | None = None
    size_unit: str | None = None


class Store(BaseModel):
    """The store a receipt was issued by."""

    name: str = "Kaufland"  # or "REWE" -- see parse_rewe.py
    street: str | None = None
    city: str | None = None
    postal_code: str | None = None


class Receipt(BaseModel):
    """One complete receipt, from any ingestion path."""

    receipt_id: str  # stable, unique id used for dedupe (see store.py)
    purchased_at: datetime
    store: Store = Field(default_factory=Store)
    line_items: list[LineItem] = Field(default_factory=list)
    total: Decimal
    currency: str = "EUR"

    # provenance — how this record was obtained, so we can tell PDF-parsed
    # receipts apart from API-fetched ones and re-parse just one source later.
    source: str = "pdf"  # "pdf" | "api"
    source_file: str | None = None  # path to the originating PDF, if source == "pdf"

    # The Rabattaktion block's whole-cart, K-Card-linked, manually-activated
    # spend-threshold coupon (e.g. "save EUR 5 once your cart crosses EUR 50")
    # -- never attributable to a single item, kept separate from line_items
    # for exactly that reason. None when a receipt has no such coupon.
    threshold_coupon_discount: Decimal | None = None

    def line_item_sum(self) -> Decimal:
        """Sum of line totals plus the whole-cart threshold coupon, if any --
        should equal ``total`` on a well-parsed receipt."""
        return (
            sum((li.total_price for li in self.line_items), Decimal(0))
            + (self.threshold_coupon_discount or Decimal(0))
        )

    def totals_match(self) -> bool:
        """True when parsed line items reconcile with the printed total.

        Used as the correctness gate in the verification step: a receipt whose
        line items don't add up to its total was parsed wrong.
        """
        return self.line_item_sum() == self.total
