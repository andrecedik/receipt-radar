"""Parse an EDEKA Kassenbon PDF text layer into a :class:`Receipt`.

EDEKA's digital receipt (the ``Kassenbon_YYYY-MM-DD_HH.MM.pdf`` download, an
iText-generated PDF) is a real text-layer PDF, so the same :mod:`pypdf`
extraction as for Kaufland and REWE applies. The layout rules below were
derived from two real Kassenbons (2026-08/09, two independently operated
stores); parsed line items reconcile exactly to the printed ``SUMME`` on both.

**pypdf does not return the printed order.** Each row is drawn as several
positioned text runs, and pypdf emits them in drawing order, not reading
order. The rules below match what pypdf actually returns; the printed layout
is noted alongside for orientation:

* A same-row quantity, printed ``NAME  0,85 € x 2  1,70 B``, comes out as
  ``2€ x 0,85NAME 1,70 B`` — quantity and unit price glued in front of the
  name.
* A weighed item, printed as the item row followed by
  ``0,396 kg x 6,90 €/kg``, comes out as three lines: ``kg x0,396 6,90``
  *before* the item line, then the item, then a lone ``€/kg``.

Notable EDEKA specifics, and where they differ from Kaufland/REWE:

* The item region runs from a bare ``EUR`` column header to ``SUMME €``.
* Tax classes are ``A`` = 7 % and ``B`` = 19 % — the **opposite** of Kaufland
  and REWE. Nothing downstream uses the rate, only the letter (and ``None``
  for discounts), so no mapping is needed.
* The tax letter can carry a ``*`` before it (``0,50*B`` — "Position nicht
  rabattberechtigt", i.e. no PAYBACK points; seen on Pfand) or a ``W`` after
  it (``2,08 AW``) whose meaning is not printed anywhere on the receipt. Both
  are dropped; the item is kept.
* ``SOFORTSTORNO`` voids an item the cashier scanned by mistake: the next
  line repeats it with a negative amount. Both the voided item and the void
  line are dropped, so price history never sees a purchase that did not
  happen.
* ``Coupon: N`` lines carry no amount and are ignored.
* The header names the store's operating company (``Supermarkt ... GmbH``),
  not EDEKA; "EDEKA" only appears in the footer, if at all. The
  ``Datum Uhrzeit  Filiale Pos Bed Bon`` column header is what identifies the
  receipt, with its values on the next line (``29.08.26 15:30 0214139 204
  010 2829``) — minute precision, two-digit year.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from .models import LineItem, Receipt, Store
from .parse_pdf import _extract_size, _money

_EDEKA_MARK = re.compile(r"\bEDEKA\b")
_BON_HEADER = re.compile(r"Datum\s+Uhrzeit\s+Filiale\s+Pos\s+Bed\s+Bon")
_POSTAL_CITY = re.compile(r"^(?P<plz>\d{5})\s+(?P<city>.+)$")

_ITEM = re.compile(
    r"^(?:(?P<qty>\d+)€ x ?(?P<unit>\d+,\d{2}))?"
    r"(?P<name>.+?)\s+(?P<amt>-?\d+,\d{2})\s*\*?(?P<tax>[AB])W?$"
)
_WEIGHT = re.compile(r"^kg x\s*(?P<w>\d+,\d{3})\s+(?P<unit>\d+,\d{2})$")
_STORNO = "SOFORTSTORNO"
_SUMME = re.compile(r"^SUMME\s*€\s*(?P<amt>-?\d+,\d{2})$")
_BON_LINE = re.compile(
    r"(?P<dd>\d{2})\.(?P<mm>\d{2})\.(?P<yy>\d{2})\s+(?P<hh>\d{2}):(?P<mi>\d{2})"
    r"\s+(?P<filiale>\d+)\s+(?P<pos>\d+)\s+(?P<bed>\d+)\s+(?P<bon>\d+)"
)


def is_edeka(text: str) -> bool:
    return _EDEKA_MARK.search(text) is not None or _BON_HEADER.search(text) is not None


def _parse_store(lines: list[str]) -> Store:
    """Header = everything before the ``EUR`` column header: operator company
    name, street, ``PLZ Stadt``, then optional phone/web lines. Street is the
    line right before the postal/city line."""
    store = Store(name="EDEKA")
    header: list[str] = []
    for ln in lines:
        if ln == "EUR":
            break
        if ln:
            header.append(ln)
    for i, ln in enumerate(header):
        if m := _POSTAL_CITY.match(ln):
            store.postal_code = m["plz"]
            store.city = m["city"].strip()
            if i > 0:
                store.street = header[i - 1]
            break
    return store


def _bon_line(text: str) -> re.Match[str] | None:
    """The values row right under the ``Datum Uhrzeit Filiale ...`` header."""
    header = _BON_HEADER.search(text)
    return _BON_LINE.search(text, header.end() if header else 0)


def _parse_line_items(lines: list[str]) -> list[LineItem]:
    items: list[LineItem] = []
    in_body = False
    pending_weight: re.Match[str] | None = None
    voiding = False
    for ln in lines:
        if not in_body:
            in_body = ln == "EUR"
            continue
        if _SUMME.match(ln):
            break
        if ln == _STORNO:
            voiding = True
            continue
        if m := _WEIGHT.match(ln):
            pending_weight = m
            continue
        if not (m := _ITEM.match(ln)):
            continue  # "€/kg", "Coupon: N", "----------Posten: N"

        name = m["name"].strip()
        total = _money(m["amt"])
        if voiding:
            voiding = False
            # Drop the most recent matching item: the void line repeats its
            # name with the amount negated.
            for i in range(len(items) - 1, -1, -1):
                if items[i].name == name and items[i].total_price == -total:
                    del items[i]
                    break
            pending_weight = None
            continue

        size_value, size_unit = _extract_size(name)
        item = LineItem(
            name=name, unit_price=total, total_price=total,
            tax_class=m["tax"], size_value=size_value, size_unit=size_unit)
        if m["qty"]:
            item.quantity = Decimal(m["qty"])
            item.unit_price = _money(m["unit"])
        elif pending_weight:
            weight = _money(pending_weight["w"])
            item.quantity = weight
            item.unit_price = _money(pending_weight["unit"])
            item.size_value, item.size_unit = weight, "kg"
        pending_weight = None
        items.append(item)
    return items


def parse_text(text: str, *, source_file: str | None = None, label: str = "receipt") -> Receipt:
    """Parse an already-extracted EDEKA Kassenbon text layer into a
    :class:`Receipt`.

    Raises ``ValueError`` if the text is not a recognisable EDEKA receipt or
    if the total/date cannot be found -- better to fail loudly than to store a
    half-parsed receipt.
    """
    if not is_edeka(text):
        raise ValueError(f"{label}: does not look like an EDEKA receipt")

    lines = [ln.strip() for ln in text.splitlines()]

    bon = _bon_line(text)
    if bon is None:
        raise ValueError(f"{label}: could not find the date/Filiale/Bon line")
    purchased_at = datetime(
        2000 + int(bon["yy"]), int(bon["mm"]), int(bon["dd"]),
        int(bon["hh"]), int(bon["mi"]))

    summe = next((m for ln in lines if (m := _SUMME.match(ln))), None)
    if not summe:
        raise ValueError(f"{label}: could not find the total (SUMME)")

    return Receipt(
        # Filiale-Pos-YYYYMMDD-Bon: Bon numbers reset per till, so
        # store+till+date+Bon is what makes it unique.
        receipt_id=f"edeka-{bon['filiale']}-{bon['pos']}-{purchased_at:%Y%m%d}-{bon['bon']}",
        purchased_at=purchased_at,
        store=_parse_store(lines),
        line_items=_parse_line_items(lines),
        total=_money(summe["amt"]),
        source="pdf",
        source_file=source_file,
    )
