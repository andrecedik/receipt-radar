"""Parse a receipt PDF into a :class:`Receipt`.

:func:`parse_text` is the retailer dispatcher: it recognises which chain
printed the receipt and hands off to that chain's parser -- REWE lives in
:mod:`parse_rewe`; Kaufland's rules are the rest of this module.

Kaufland's "als PDF speichern" export is a real text-layer PDF, so we extract
text with :mod:`pypdf` (no OCR) and apply the layout rules below.

The rules were derived from and validated against real digital receipts
(2026-08): parsed line items reconcile exactly to the printed ``Summe`` on
every sample. Notable Kaufland specifics:

* Tax classes are ``A`` = 19 % and ``B`` = 7 % (the opposite of the convention
  in some paper-receipt parsers).
* A line item can appear in four shapes: simple (``NAME  PRICE TAX``), inline
  quantity (``NAME  Q * UNIT  TOTAL TAX``), two-line quantity (name on its own
  line, ``Q * UNIT  TOTAL TAX`` on the next) and two-line weight
  (``W,WWW kg  TOTAL TAX`` on the next line).
* Discount lines (negative, no tax letter — e.g. ``K Card XTRA Rabatt``,
  ``Mengenrabatt``, ``Artikelrabatt``) are kept as negative line items so the
  receipt reconciles. A final discount can also appear in a ``Rabattaktion``
  block between ``Zwischensumme`` and ``Summe``.
* The printed ``qty * unit`` price is always unsigned, even on a refund line
  (e.g. a Leergut/Pfand return priced per item); the sign is inferred from
  the line total instead.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from .models import LineItem, Receipt, Store

TAX_RATES = {"A": Decimal("0.19"), "B": Decimal("0.07")}


def _money(raw: str) -> Decimal:
    """German number format -> Decimal. '103,62' -> Decimal('103.62')."""
    return Decimal(raw.replace(".", "").replace(",", "."))


def _signed_unit_price(unit_raw: str, total: Decimal) -> Decimal:
    """The 'qty * unit' field is always printed unsigned, even on refund
    lines like Pfand/Leergut returns, where the line total is negative
    (e.g. "Leergut Mopro  2 * 0,25  -0,50 B" — 0,25 per bottle deposit,
    -0,50 total). Flip the sign to match the total so unit_price * quantity
    reconciles."""
    unit = _money(unit_raw)
    return -unit if total < 0 < unit else unit


def extract_text(pdf_path: Path) -> str:
    """Pull the full text layer out of the PDF, page by page."""
    reader = PdfReader(str(pdf_path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


# --- line patterns (anchored on the trailing "amount + tax letter") ---
_INLINE_Q = re.compile(
    r"^(?P<name>.+?)\s+(?P<qty>\d+)\s*\*\s*(?P<unit>\d+,\d{2})"
    r"\s+(?P<amt>-?\d+,\d{2})\s+(?P<tax>[AB])$"
)
_CONT_Q = re.compile(
    r"^(?P<qty>\d+)\s*\*\s*(?P<unit>\d+,\d{2})\s+(?P<amt>-?\d+,\d{2})\s+(?P<tax>[AB])$"
)
_CONT_W = re.compile(r"^(?P<w>\d+,\d{3})\s*kg\s+(?P<amt>-?\d+,\d{2})\s+(?P<tax>[AB])$")
# Same weight-priced shape as _CONT_W, but with the name still on the same
# line — pypdf occasionally merges the two logical rows for a weighed item
# into one text line depending on column layout.
_INLINE_W = re.compile(
    r"^(?P<name>.+?)\s+(?P<w>\d+,\d{3})\s*kg\s+(?P<amt>-?\d+,\d{2})\s+(?P<tax>[AB])$"
)
_SIMPLE = re.compile(r"^(?P<name>.+?)\s+(?P<amt>-?\d+,\d{2})\s+(?P<tax>[AB])$")
# Any discount line — "K Card XTRA Rabatt" (loyalty), "Mengenrabatt" (bulk
# quantity discount), "Artikelrabatt" (the Rabattaktion block's promo
# discount), and presumably others not yet seen. All share the same shape:
# name, negative amount, and — unlike every product/refund line — no
# trailing tax letter, which is what distinguishes a discount from a Pfand/
# Leergut refund (always negative *with* a tax letter).
_DISCOUNT = re.compile(r"^(?P<name>.+?)\s+(?P<amt>-\d+,\d{2})$")

# A pack size embedded in the product name itself, e.g. "750g", "2,5kg",
# "425ml" — the last such token in the name wins, since Kaufland prints it
# right before (or as) the trailing qualifier.
_SIZE_IN_NAME = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l)(?!\w)")


def _extract_size(name: str) -> tuple[Decimal | None, str | None]:
    matches = list(_SIZE_IN_NAME.finditer(name))
    if not matches:
        return None, None
    m = matches[-1]
    return Decimal(m.group(1).replace(",", ".")), m.group(2).lower()


def _size_for(name: str, *, weight: Decimal | None = None) -> tuple[Decimal | None, str | None]:
    """Weight-priced lines are unambiguous — Kaufland only ever weighs in kg."""
    if weight is not None:
        return weight, "kg"
    return _extract_size(name)

_SUMME = re.compile(r"^Summe\s+(?P<amt>-?\d+,\d{2})$")
_ZWISCHENSUMME = re.compile(r"^Zwischensumme\s+\d+,\d{2}$")
_DATE = re.compile(r"Datum:\s*(\d{2})\.(\d{2})\.(\d{2,4})\s+Zeit:\s*(\d{2}):(\d{2}):(\d{2})")
_RECEIPT_NO = re.compile(r"Bon:\s*(\d+)")
_FILIALE = re.compile(r"Filiale:\s*(\d+)\s+Kasse:\s*(\d+)")
_POSTAL_CITY = re.compile(r"^(?P<plz>\d{5})\s+(?P<city>.+)$")


def is_kaufland(text: str) -> bool:
    # Scans the whole receipt, not just a leading window: the "Kaufland - "
    # store-line prefix and the "saved ... with Kaufland Card" loyalty line
    # are both optional (e.g. absent when no card discount applied that
    # trip), and on a longer receipt the payment footer's "Kaufland Card
    # XTRA:" / "Kaufland DE <Filiale>" lines can land 30+ lines in.
    return "kaufland" in text.lower()


def _parse_store(lines: list[str]) -> Store:
    store = Store()
    for ln in lines[:8]:
        if ln.startswith("Kaufland") and "-" in ln:
            store.street = ln.split("-", 1)[1].strip()
        m = _POSTAL_CITY.match(ln)
        if m:
            store.postal_code = m["plz"]
            store.city = m["city"].strip()
    return store


def _parse_datetime(text: str) -> datetime | None:
    m = _DATE.search(text)
    if not m:
        return None
    dd, mm, yy, hh, mi, ss = m.groups()
    year = int(yy) if len(yy) == 4 else 2000 + int(yy)
    return datetime(year, int(mm), int(dd), int(hh), int(mi), int(ss))


def _receipt_id(text: str, purchased_at: datetime) -> str:
    """Stable, globally-unique id: Filiale-Kasse-YYYYMMDD-Bon.

    All four components are always printed, and together they uniquely identify a
    receipt (Bon numbers reset per till/day, so store+till+date+Bon is unique).
    """
    fil = _FILIALE.search(text)
    bon = _RECEIPT_NO.search(text)
    store_no = fil.group(1) if fil else "x"
    till = fil.group(2) if fil else "x"
    bon_no = bon.group(1) if bon else purchased_at.strftime("%H%M%S")
    return f"kaufland-{store_no}-{till}-{purchased_at:%Y%m%d}-{bon_no}"


def _parse_line_items(lines: list[str]) -> tuple[list[LineItem], Decimal | None]:
    """Walk the body between the 'Preis EUR' header and 'Summe'.

    Products are captured at their printed price; per-item discount lines
    (see ``_DISCOUNT``) are captured as negative line items -- this is what
    makes the line-item sum reconcile to the printed total. Discount lines
    seen after ``Zwischensumme`` belong to the ``Rabattaktion`` block's
    whole-cart threshold coupon, not any single product, so they're summed
    separately instead of becoming a LineItem (see
    ``Receipt.threshold_coupon_discount``).
    """
    items: list[LineItem] = []
    pending_name: str | None = None
    in_body = False
    past_zwischensumme = False
    threshold_coupon_discount: Decimal | None = None

    for ln in lines:
        if "Preis EUR" in ln:
            in_body = True
            continue
        if not in_body:
            continue
        if _SUMME.match(ln):  # end of the item region
            break
        if _ZWISCHENSUMME.match(ln):
            past_zwischensumme = True
            continue

        if m := _DISCOUNT.match(ln):
            if past_zwischensumme:
                amt = _money(m["amt"])
                threshold_coupon_discount = (
                    amt if threshold_coupon_discount is None
                    else threshold_coupon_discount + amt
                )
                continue
            size_value, size_unit = _size_for(m["name"])
            items.append(LineItem(
                name=m["name"], total_price=_money(m["amt"]),
                size_value=size_value, size_unit=size_unit))
            pending_name = None
            continue
        if m := _INLINE_Q.match(ln):
            name = m["name"].strip()
            total = _money(m["amt"])
            size_value, size_unit = _size_for(name)
            items.append(LineItem(
                name=name, quantity=Decimal(m["qty"]),
                unit_price=_signed_unit_price(m["unit"], total), total_price=total,
                tax_class=m["tax"], size_value=size_value, size_unit=size_unit))
            pending_name = None
            continue
        if m := _INLINE_W.match(ln):
            name = m["name"].strip()
            weight = _money(m["w"])
            size_value, size_unit = _size_for(name, weight=weight)
            items.append(LineItem(
                name=name, quantity=weight, total_price=_money(m["amt"]),
                tax_class=m["tax"], size_value=size_value, size_unit=size_unit))
            pending_name = None
            continue
        if (m := _CONT_Q.match(ln)) and pending_name:
            total = _money(m["amt"])
            size_value, size_unit = _size_for(pending_name)
            items.append(LineItem(
                name=pending_name, quantity=Decimal(m["qty"]),
                unit_price=_signed_unit_price(m["unit"], total), total_price=total,
                tax_class=m["tax"], size_value=size_value, size_unit=size_unit))
            pending_name = None
            continue
        if (m := _CONT_W.match(ln)) and pending_name:
            weight = _money(m["w"])
            size_value, size_unit = _size_for(pending_name, weight=weight)
            items.append(LineItem(
                name=pending_name, quantity=weight,
                total_price=_money(m["amt"]), tax_class=m["tax"],
                size_value=size_value, size_unit=size_unit))
            pending_name = None
            continue
        if m := _SIMPLE.match(ln):
            name = m["name"].strip()
            size_value, size_unit = _size_for(name)
            items.append(LineItem(
                name=name, unit_price=_money(m["amt"]),
                total_price=_money(m["amt"]), tax_class=m["tax"],
                size_value=size_value, size_unit=size_unit))
            pending_name = None
            continue

        # A bare product-name line (no price yet) — buffer it for the
        # continuation line that carries the quantity/weight and price.
        if re.search(r"[A-Za-zÄÖÜäöü]", ln) and not any(
            kw in ln for kw in ("Zwischensumme", "Steuer", "Kaufland Pay", "----")
        ):
            pending_name = ln
    return items, threshold_coupon_discount


def parse_text(text: str, *, source_file: str | None = None, label: str = "receipt") -> Receipt:
    """Parse an already-extracted receipt text layer into a :class:`Receipt`,
    whichever supported retailer printed it.

    Split out from :func:`parse_pdf` so the format logic is testable against a
    committed text fixture, with no PDF or filesystem involved.

    Raises ``ValueError`` if the text is not a recognisable Kaufland or REWE
    receipt or if the total/date cannot be found — better to fail loudly than
    to store a half-parsed receipt.
    """
    # Kaufland receipts from before roughly July 2024 are exported by the app
    # as its rendered "Receipt Copy" screen: every row is an image tile, no
    # text layer at all, so pypdf hands back "". (Observed boundary on real
    # data: last image-only receipt 2024-06-13, first text-based one
    # 2024-07-20.) Say so, instead of letting that fall through to the "not a
    # recognised receipt" check below and sending the user to look for the
    # wrong problem.
    if not text.strip():
        raise ValueError(
            f"{label}: PDF has no text layer (image-only). Receipts from before "
            "July 2024 are exported as images by the Kaufland app and can't be "
            "parsed yet; receipts from July 2024 onward are text-based and work."
        )

    # Imported here, not at module level: parse_rewe borrows the number/size
    # helpers from this module, so a top-level import would be circular.
    from . import parse_rewe

    if parse_rewe.is_rewe(text):
        return parse_rewe.parse_text(text, source_file=source_file, label=label)
    if not is_kaufland(text):
        raise ValueError(f"{label}: does not look like a Kaufland or REWE receipt")
    return _parse_kaufland(text, source_file=source_file, label=label)


def _parse_kaufland(text: str, *, source_file: str | None, label: str) -> Receipt:
    lines = [ln.strip() for ln in text.splitlines()]

    purchased_at = _parse_datetime(text)
    if purchased_at is None:
        raise ValueError(f"{label}: could not find the Datum/Zeit line")

    summe = next((m for ln in lines if (m := _SUMME.match(ln))), None)
    if not summe:
        raise ValueError(f"{label}: could not find the total (Summe)")

    line_items, threshold_coupon_discount = _parse_line_items(lines)

    return Receipt(
        receipt_id=_receipt_id(text, purchased_at),
        purchased_at=purchased_at,
        store=_parse_store(lines),
        line_items=line_items,
        threshold_coupon_discount=threshold_coupon_discount,
        total=_money(summe["amt"]),
        source="pdf",
        source_file=source_file,
    )


def parse_pdf(pdf_path: Path) -> Receipt:
    """Parse one receipt PDF into a :class:`Receipt`."""
    pdf_path = Path(pdf_path)
    return parse_text(
        extract_text(pdf_path), source_file=str(pdf_path), label=pdf_path.name
    )
