"""Parse a REWE eBon PDF text layer into a :class:`Receipt`.

REWE's eBon (the "stationary-ebon-<uuid>.pdf" download from the REWE app /
rewe.de) is a real text-layer PDF, so the same :mod:`pypdf` extraction as
for Kaufland applies. The layout rules below were derived from six real eBons
(2025-07 .. 2026-07, incl. a Getränkemarkt and a franchise partner store);
parsed line items reconcile exactly to the printed ``SUMME`` on every one.
Notable REWE specifics, and where they differ from Kaufland:

* The item region runs from a bare ``EUR`` column header to ``SUMME``.
  Tax classes are ``A`` = 19 % and ``B`` = 7 %, like Kaufland.
* A multi-unit item prints its total first and the quantity *after* it, on
  an indented continuation line: ``NAME  TOTAL TAX`` / ``2 Stk x  1,79``.
  Weighed items follow the same shape with ``0,500 kg x  2,50 EUR/kg`` (that
  variant is REWE's documented format but not yet seen on a real sample).
* Pfand and Leergut lines carry a trailing ``*`` ("no Bonus on * items"),
  which is stripped; they stay ordinary taxed line items.
* A per-item discount (``GRATIS ...``, promo Rabatt) is printed *with* a tax
  letter, indented one space under its product. It is emitted with
  ``tax_class=None`` regardless, because that is how the rest of the app
  tells a discount from a refund (see ``price_integrity.attach_item_discounts``).
* Date, time, Bon number and Markt/Kasse live on two lines near the end
  (``DD.MM.YYYY  HH:MM  Bon-Nr.:N`` / ``Markt:N  Kasse:N  Bed.:N``); the
  card slip's ``Uhrzeit`` (with seconds) is only present on card payments,
  so time is taken from the Bon line at minute precision.
* The "Deine zusätzlichen Vorteile heute" footer block has its own
  ``Sonstige Vorteile`` / ``Summe`` lines — lower-case ``Summe``, unlike the
  receipt's ``SUMME`` — and Bonus-Guthaben collected is informational, not a
  deduction; both are ignored.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from .models import LineItem, Receipt, Store
from .parse_pdf import _extract_size, _money

_REWE_MARK = re.compile(r"\bR ?E ?W ?E\b")
_STORE_NAME = re.compile(r"^R ?E ?W ?E\b")
_UID = re.compile(r"^UID Nr\.:")
_POSTAL_CITY = re.compile(r"^(?P<plz>\d{5})\s+(?P<city>.+)$")

# Item lines: the tax letter anchors the end; an optional trailing "*" marks
# Pfand/Leergut. Raw (unstripped) lines are matched so the one-space indent
# of a discount line is still visible.
_ITEM = re.compile(
    r"^(?P<indent> ?)(?P<name>\S.*?)\s+(?P<amt>-?\d+,\d{2})\s+(?P<tax>[AB])(?:\s+\*)?\s*$"
)
_CONT_Q = re.compile(r"^\s+(?P<qty>\d+)\s+Stk\s+x\s+(?P<unit>\d+,\d{2})\s*$")
_CONT_W = re.compile(
    r"^\s+(?P<w>\d+,\d{3})\s+kg\s+x\s+(?P<unit>\d+,\d{2})\s+EUR/kg\s*$"
)
_SUMME = re.compile(r"^\s*SUMME\s+EUR\s+(?P<amt>-?\d+,\d{2})\s*$")
_BON_LINE = re.compile(
    r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})\s+Bon-Nr\.:\s*(\d+)"
)
_MARKT_LINE = re.compile(r"Markt:\s*(\d+)\s+Kasse:\s*(\d+)")


def is_rewe(text: str) -> bool:
    # The store-name header is optional (one sample only shows the address
    # and a "Haben Sie schon REWE Bonus?" box) and may be letter-spaced
    # ("R E W E"), so match either spelling anywhere: the Bonus footer and the
    # TSE "Seriennnummer Kasse: REWE:..." line are always there as fallback.
    return _REWE_MARK.search(text) is not None


def _parse_store(lines: list[str]) -> Store:
    """Header = everything before ``UID Nr.:``: optional store-name line,
    street, ``PLZ Stadt``, optional phone. Street is the line right before
    the postal/city line."""
    store = Store(name="REWE")
    header: list[str] = []
    for ln in lines:
        if _UID.match(ln):
            break
        if ln:
            header.append(ln)
    for i, ln in enumerate(header):
        m = _POSTAL_CITY.match(ln)
        if m:
            store.postal_code = m["plz"]
            store.city = m["city"].strip()
            if i > 0 and not _STORE_NAME.match(header[i - 1]):
                store.street = header[i - 1]
            break
    return store


def _parse_datetime(text: str) -> datetime | None:
    m = _BON_LINE.search(text)
    if not m:
        return None
    dd, mm, yyyy, hh, mi, _bon = m.groups()
    return datetime(int(yyyy), int(mm), int(dd), int(hh), int(mi))


def _receipt_id(text: str, purchased_at: datetime) -> str:
    """Stable, globally-unique id: Markt-Kasse-YYYYMMDD-Bon (Bon numbers reset
    per till, so store+till+date+Bon is what makes it unique)."""
    markt = _MARKT_LINE.search(text)
    bon = _BON_LINE.search(text)
    store_no = markt.group(1) if markt else "x"
    till = markt.group(2) if markt else "x"
    bon_no = bon.group(6) if bon else purchased_at.strftime("%H%M")
    return f"rewe-{store_no}-{till}-{purchased_at:%Y%m%d}-{bon_no}"


def _parse_line_items(raw_lines: list[str]) -> list[LineItem]:
    """Walk the body between the ``EUR`` column header and ``SUMME``.

    A ``Stk x`` / ``kg x`` continuation line patches the quantity and unit
    price onto the *previous* item (REWE prints the total first)."""
    items: list[LineItem] = []
    in_body = False
    for raw in raw_lines:
        ln = raw.strip()
        if not in_body:
            if ln == "EUR":
                in_body = True
            continue
        if _SUMME.match(raw):
            break

        if (m := _CONT_Q.match(raw)) and items:
            prev = items[-1]
            prev.quantity = Decimal(m["qty"])
            prev.unit_price = _money(m["unit"])
            continue
        if (m := _CONT_W.match(raw)) and items:
            prev = items[-1]
            weight = _money(m["w"])
            prev.quantity = weight
            prev.unit_price = _money(m["unit"])
            prev.size_value, prev.size_unit = weight, "kg"
            continue
        if m := _ITEM.match(raw):
            name = m["name"].strip()
            total = _money(m["amt"])
            size_value, size_unit = _extract_size(name)
            if m["indent"] and total < 0:
                # Indented + negative = a discount on the line above. Refunds
                # (Leergut) are negative too but start in column 0.
                items.append(LineItem(
                    name=name, total_price=total,
                    size_value=size_value, size_unit=size_unit))
                continue
            items.append(LineItem(
                name=name, unit_price=total, total_price=total,
                tax_class=m["tax"], size_value=size_value, size_unit=size_unit))
    return items


def parse_text(text: str, *, source_file: str | None = None, label: str = "receipt") -> Receipt:
    """Parse an already-extracted REWE eBon text layer into a :class:`Receipt`.

    Raises ``ValueError`` if the text is not a recognisable REWE receipt or if
    the total/date cannot be found -- better to fail loudly than to store a
    half-parsed receipt.
    """
    if not is_rewe(text):
        raise ValueError(f"{label}: does not look like a REWE receipt")

    raw_lines = text.splitlines()
    lines = [ln.strip() for ln in raw_lines]

    purchased_at = _parse_datetime(text)
    if purchased_at is None:
        raise ValueError(f"{label}: could not find the date/Bon-Nr. line")

    summe = next((m for ln in raw_lines if (m := _SUMME.match(ln))), None)
    if not summe:
        raise ValueError(f"{label}: could not find the total (SUMME)")

    return Receipt(
        receipt_id=_receipt_id(text, purchased_at),
        purchased_at=purchased_at,
        store=_parse_store(lines),
        line_items=_parse_line_items(raw_lines),
        total=_money(summe["amt"]),
        source="pdf",
        source_file=source_file,
    )
