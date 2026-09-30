"""Parse OCR'd Lidl app receipts into a :class:`Receipt`.

The Lidl app has no PDF export. "Export receipt" scrolls through the in-app
receipt, screenshots it, stitches the screenshots together and shares a single
tall PNG (see :mod:`ocr`). That image holds a monospace TSE receipt block
(item lines, ``zu zahlen``, the ``MWST%`` table, TSE signature data, the
``Filiale Bon/Kasse Datum Zeit`` line) surrounded by app UI. So this module
parses *OCR text*, and OCR is lossy: Tesseract reliably misreads ``0`` as
``6`` or ``8`` in this font (``-0,76`` -> ``-8,76``).

A misread price stored silently would poison price history, so a receipt is
only accepted when it reconciles, and the receipt's own redundancy is used to
repair digit confusions first:

* ``unit x qty = line total`` (and ``weight x EUR/kg = line total``) must hold
  for every multi-quantity and weighed line;
* the ``MWST%`` table prints the gross amount per tax class, which the line
  items of that class (discounts count toward the class of the item above
  them) must sum to;
* the grand total (``zu zahlen``, ``Lidl Pay``, ``Summe``) must equal the sum;
* when printed, ``Gesamter Preisvorteil`` must equal the sum of the discounts.
  This independent check exists because the repair search can *manufacture* a
  match: a misread ``1,09`` and a misread discount ``-0,04`` -> ``-0,84`` can
  cancel exactly, and a dropped discount line can be "explained" by rewriting a
  price (``1,69`` -> ``1,09`` offsets a lost ``-0,60``). Digit errors in this
  font come in multiples of 0.08 / 0.80 / 8, so such cancellations are common.

Repairs only substitute confusable digits (:data:`_CONFUSABLE`), use as few
substitutions as possible, and are applied only when exactly one solution
exists. Anything else raises ``ValueError`` -- better no receipt than a wrong
one.

Even so, one OCR reading is not trusted on its own: :func:`parse_readings`
takes several independent Tesseract readings of the same image and accepts the
receipt only if at least two reconcile and every one that does agrees on every
amount.

Line shapes (after OCR normalisation)::

    Testbrot 1,20 A                       simple
    Testjoghurt 0,79 x 2 1,58 A           unit x qty, total
    Testbanane lose 1,55 A                weighed: the next line is
    1,200 kg x 1,29 EUR/kg                  "<kg> x <unit> EUR/kg"
    Pfand 0,25 EM 0,25 x 2 0,50 B         deposit: label is part of the name
    Lidl Plus Rabatt -0,22                discount: negative, no tax letter
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import combinations, product

from .models import LineItem, Receipt, Store
from .parse_pdf import _money, _size_for

# What Tesseract's output digit may really have been, in this font. One-way on
# purpose: it reads a true 0 as 8 or 6, not the reverse, and allowing both
# directions makes repairs ambiguous (-8,46 -> -0,46 vs 0,99 -> 8,99 shift the
# sum identically), which means rejection. Extend when a new confusion is seen
# on a real receipt, not speculatively.
_CONFUSABLE = {"8": "0", "6": "0"}
_MAX_EDITS = 3  # per tax class

_QTY = re.compile(
    r"^(?P<name>.+?)\s+(?P<unit>\d+,\d{2})\s+x\s+(?P<qty>\d+)\s+(?P<amt>-?\d+,\d{2})\s+(?P<tax>[A-D])$"
)
_SIMPLE = re.compile(r"^(?P<name>.+?)\s+(?P<amt>\d+,\d{2})\s+(?P<tax>[A-D])$")
_DISCOUNT = re.compile(r"^(?P<name>.+?)\s+(?P<amt>-\d+,\d{2})$")
_WEIGHT = re.compile(r"^(?P<w>\d+,\d{3,4})\s*kg\s+x\s+(?P<unit>\d+,\d{2})\s*EUR/kg$")
_PREISVORTEIL = re.compile(r"^Gesamter Preisvorteil\s+(?P<amt>\d+,\d{2})$")
_TOTAL = re.compile(r"^(?:zu zahlen|Lidl Pay)\s+(?P<amt>\d+,\d{2})$")
_SUMME = re.compile(r"^Summe\s+\d+,\d{2}\s+\d+,\d{2}\s+(?P<amt>\d+,\d{2})$")
_TAX_ROW = re.compile(
    r"^(?P<cls>[A-D])\s*(?P<rate>\d+)\s*%\s+(?P<vat>\d+,\d{2})\s+(?P<net>\d+,\d{2})\s+(?P<gross>\d+,\d{2})$"
)
# "5567 511555/03 05.01.26 09:36" -- Filiale, Bon/Kasse, date, time. The Kasse
# digits come out unreliably (seen: "/02" read as "/082"), so they are matched
# but not used in the receipt id.
_ID_LINE = re.compile(
    r"^(?P<store>\d+)\s+(?P<bon>\d+)/\d+\s+"
    r"(?P<dd>\d{2})\.(?P<mm>\d{2})\.(?P<yy>\d{2,4})\s+(?P<hh>\d{2}):(?P<mi>\d{2})$"
)
_POSTAL_CITY = re.compile(r"^(?P<plz>\d{5})\s+(?P<city>.+)$")


def is_lidl(text: str) -> bool:
    return "lidl" in text.lower()


def _normalise(line: str) -> str:
    """Undo OCR spacing/glyph noise that has a single obvious reading."""
    line = line.strip()
    line = re.sub(r"(?<=[\d,-])@|@(?=[\d,])", "0", line)  # '@' is a zero
    line = re.sub(r"(\d), (\d)", r"\1,\2", line)  # "-8, 84"
    line = re.sub(r"(\d,\d{2})x\b", r"\1 x", line)  # "1,99x 2"
    line = re.sub(r"\bx(?=\d)", "x ", line)
    # OCR sometimes appends a stray digit to a price ("1,708 A"); EUR has two
    # decimals, and weights (three) are followed by "kg", never by a line end.
    line = re.sub(r"(-?\d+,\d{2})\d(?=(?:\s+[A-D])?$)", r"\1", line)
    # ...and sometimes an extra digit in the integer part ("-08,58", "06,25"); a
    # price never prints a leading zero there, so the extra digit is noise.
    line = re.sub(r"(?<![\d,.:/-])(-?)0\d+,(\d{2})\b", r"\g<1>0,\2", line)
    return line


@dataclass
class _Row:
    name: str
    amt: str  # signed, as printed, e.g. "-0,76" -- edited in place by repair
    tax: str | None  # own letter; discounts get the letter of the item above
    is_discount: bool = False
    unit: str | None = None
    qty: int | None = None
    weight: str | None = None

    def locally_ok(self) -> bool:
        amt = _money(self.amt)
        if self.qty is not None:
            return _money(self.unit) * self.qty == amt
        if self.weight is not None:
            return abs(_money(self.weight) * _money(self.unit) - amt) <= Decimal("0.0051")
        return True


def _alternatives(s: str, max_cost: int) -> list[tuple[int, str]]:
    """Every string reachable from ``s`` by fixing 1..``max_cost`` confusable
    digits, as ``(digits changed, string)``."""
    spots = [i for i, ch in enumerate(s) if ch in _CONFUSABLE]
    out = []
    for cost in range(1, max_cost + 1):
        for chosen in combinations(spots, cost):
            chars = list(s)
            for i in chosen:
                chars[i] = _CONFUSABLE[chars[i]]
            out.append((cost, "".join(chars)))
    return out


def _parse_rows(lines: list[str]) -> list[_Row]:
    rows: list[_Row] = []
    for ln in lines:
        if m := _WEIGHT.match(ln):
            if not rows or rows[-1].is_discount or rows[-1].weight is not None:
                raise ValueError(f"weight line without an item above it: {ln!r}")
            rows[-1].weight = m["w"][: m["w"].index(",") + 4]  # stray extra digit: keep 3 decimals
            rows[-1].unit = m["unit"]
        elif m := _QTY.match(ln):
            rows.append(_Row(m["name"], m["amt"], m["tax"], unit=m["unit"], qty=int(m["qty"])))
        elif m := _SIMPLE.match(ln):
            rows.append(_Row(m["name"], m["amt"], m["tax"]))
        elif (m := _DISCOUNT.match(ln)) and rows:
            # A single-line discount of 6 or 8 whole euros is far less likely
            # than "-0,xx" misread -- and in practice nearly every discount on
            # a receipt is, so leaving this to the repair search would blow its
            # edit budget. If it is wrong the receipt fails to reconcile.
            amt = re.sub(r"^-[68],", "-0,", m["amt"])
            rows.append(_Row(m["name"], amt, rows[-1].tax, is_discount=True))
        elif re.search(r"\d,\d{2}", ln):
            raise ValueError(f"unrecognised line in the item block: {ln!r}")
    return rows


def _fix_row(row: _Row) -> None:
    """Repair a row whose own arithmetic fails, when exactly one cheapest
    (<= 2 digits) substitution across its fields makes it consistent."""
    if row.locally_ok():
        return
    fields = [f for f in ("weight", "unit", "amt") if getattr(row, f)]
    options = [[(0, getattr(row, f))] + _alternatives(getattr(row, f), 2) for f in fields]
    found: dict[int, list[tuple[str, ...]]] = {}
    for combo in product(*options):
        cost = sum(c for c, _ in combo)
        if 0 < cost <= 2:
            trial = _Row(**{**row.__dict__, **{f: v for f, (_, v) in zip(fields, combo)}})
            if trial.locally_ok():
                found.setdefault(cost, []).append(tuple(v for _, v in combo))
    if found:
        best = found[min(found)]
        if len(best) == 1:
            for f, v in zip(fields, best[0]):
                setattr(row, f, v)


def _solve(rows: list[_Row], idxs: list[int], diff: Decimal) -> list[tuple[tuple[int, str], ...]]:
    """Cheapest (fewest digits changed) substitutions of free amounts -- rows
    whose amount no local rule pins -- that shift the group's sum by ``diff``.
    Returns every solution at the minimal cost (empty if none within
    ``_MAX_EDITS`` digits)."""
    states: dict[tuple[int, Decimal], list[tuple[tuple[int, str], ...]]] = {(0, Decimal(0)): [()]}
    for i in idxs:
        r = rows[i]
        if r.qty is not None or r.weight is not None:
            continue  # pinned by unit x qty / weight x price
        alts = [
            (cost, _money(alt) - _money(r.amt), alt)
            for cost, alt in _alternatives(r.amt, _MAX_EDITS)
        ]
        grown = {k: list(v) for k, v in states.items()}
        for (cost0, delta0), combos in states.items():
            for cost, d, alt in alts:
                if cost0 + cost <= _MAX_EDITS:
                    grown.setdefault((cost0 + cost, delta0 + d), []).extend(
                        c + ((i, alt),) for c in combos
                    )
        states = grown
    for cost in range(1, _MAX_EDITS + 1):
        if (cost, diff) in states:
            return states[(cost, diff)]
    return []


def _repaired_tax_row(m: re.Match) -> tuple[Decimal, Decimal, Decimal] | None:
    """(vat, net, gross) of a MWST% row once it passes its own arithmetic
    (net + vat = gross, vat = gross x rate / (100 + rate)), repairing up to two
    confusable digits when exactly one repair works; None otherwise."""
    rate = Decimal(m["rate"])

    def valid(vat: Decimal, net: Decimal, gross: Decimal) -> bool:
        return vat + net == gross and abs(gross * rate / (100 + rate) - vat) <= Decimal("0.011")

    raw = (m["vat"], m["net"], m["gross"])
    if valid(*map(_money, raw)):
        return tuple(map(_money, raw))
    options = [[(0, v)] + _alternatives(v, 2) for v in raw]
    found = {
        tuple(_money(v) for _, v in combo)
        for combo in product(*options)
        if 0 < sum(c for c, _ in combo) <= 2 and valid(*(_money(v) for _, v in combo))
    }
    return found.pop() if len(found) == 1 else None


def _trusted_tax_table(lines: list[str], total: Decimal) -> dict[str, Decimal] | None:
    """Gross per tax class from the MWST% table, or None when the table is
    missing or unrepairable (then OCR garbled it too)."""
    table: dict[str, Decimal] = {}
    for ln in lines:
        if m := _TAX_ROW.match(ln):
            row = _repaired_tax_row(m)
            if row is None:
                return None
            table[m["cls"]] = row[2]
    if not table or sum(table.values()) != total:
        return None
    return table


def _grand_total(lines: list[str]) -> Decimal | None:
    seen = [
        _money(m["amt"])
        for ln in lines
        if (m := _TOTAL.match(ln) or _SUMME.match(ln))
    ]
    if not seen:
        return None
    value, count = Counter(seen).most_common(1)[0]
    # zu zahlen comes first; if nothing repeats, it is the best single witness.
    return value if count > 1 else seen[0]


def parse_text(text: str, *, source_file: str | None = None, label: str = "receipt") -> Receipt:
    lines = [_normalise(ln) for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    id_line = next((m for ln in lines if (m := _ID_LINE.match(ln))), None)
    if id_line is None:
        raise ValueError(f"{label}: could not find the date/time line (Filiale Bon/Kasse Datum Zeit)")
    year = int(id_line["yy"]) if len(id_line["yy"]) == 4 else 2000 + int(id_line["yy"])
    purchased_at = datetime(
        year, int(id_line["mm"]), int(id_line["dd"]), int(id_line["hh"]), int(id_line["mi"])
    )

    total = _grand_total(lines)
    if total is None:
        raise ValueError(f"{label}: could not find the total (zu zahlen)")

    try:
        start = next(i for i, ln in enumerate(lines) if ln == "EUR") + 1
        end = next(i for i, ln in enumerate(lines) if ln.startswith("zu zahlen"))
    except StopIteration:
        raise ValueError(f"{label}: could not find the item block (EUR ... zu zahlen)") from None
    try:
        rows = _parse_rows(lines[start:end])
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}") from exc

    for row in rows:
        _fix_row(row)

    # Global repair, one tax class at a time when the MWST table is readable.
    table = _trusted_tax_table(lines, total)
    groups = (
        {cls: [i for i, r in enumerate(rows) if r.tax == cls] for cls in table}
        if table
        else {None: list(range(len(rows)))}
    )
    targets = table if table else {None: total}
    for cls, idxs in groups.items():
        diff = targets[cls] - sum((_money(rows[i].amt) for i in idxs), Decimal(0))
        if diff == 0:
            continue
        solutions = _solve(rows, idxs, diff)
        if len(solutions) == 1:
            for i, alt in solutions[0]:
                rows[i].amt = alt

    saved = next((_money(m["amt"]) for ln in lines if (m := _PREISVORTEIL.match(ln))), None)
    discounts = sum((_money(r.amt) for r in rows if r.is_discount), Decimal(0))
    if (
        (saved is not None and -discounts != saved)
        or not all(r.locally_ok() for r in rows)
        or sum((_money(r.amt) for r in rows), Decimal(0)) != total
        or (table and any(
            sum((_money(rows[i].amt) for i in idxs), Decimal(0)) != table[cls]
            for cls, idxs in groups.items()
        ))
    ):
        raise ValueError(
            f"{label}: OCR text does not reconcile with the printed total {total} "
            "(or the printed Preisvorteil) and could not be repaired unambiguously; not stored."
        )

    return Receipt(
        receipt_id=f"lidl-{id_line['store']}-{purchased_at:%Y%m%d}-{id_line['bon']}",
        purchased_at=purchased_at,
        store=_parse_store(lines),
        line_items=[_line_item(r) for r in rows],
        total=total,
        source="image",
        source_file=source_file,
    )


def _line_item(row: _Row) -> LineItem:
    amt = _money(row.amt)
    if row.is_discount:
        return LineItem(name=row.name, total_price=amt)
    if row.weight is not None:
        weight = _money(row.weight)
        return LineItem(
            name=row.name, quantity=weight, unit_price=_money(row.unit), total_price=amt,
            tax_class=row.tax, size_value=weight, size_unit="kg",
        )
    size_value, size_unit = _size_for(row.name)
    if row.qty is not None:
        return LineItem(
            name=row.name, quantity=Decimal(row.qty), unit_price=_money(row.unit),
            total_price=amt, tax_class=row.tax, size_value=size_value, size_unit=size_unit,
        )
    return LineItem(
        name=row.name, unit_price=amt, total_price=amt, tax_class=row.tax,
        size_value=size_value, size_unit=size_unit,
    )


def _parse_store(lines: list[str]) -> Store:
    store = Store(name="Lidl")
    for i, ln in enumerate(lines[:8]):
        if m := _POSTAL_CITY.match(ln):
            store.postal_code, store.city = m["plz"], m["city"].strip()
            if i > 0 and "lidl" not in lines[i - 1].lower():
                store.street = lines[i - 1]
            break
    return store


def _amounts(r: Receipt) -> tuple:
    """Everything price-bearing, minus item names (umlauts and punctuation
    legitimately differ between language packs) and the identity fields (voted
    on separately -- a stray digit in the Filiale/Bon is common)."""
    return (
        r.total,
        tuple((li.total_price, li.quantity, li.unit_price, li.tax_class) for li in r.line_items),
    )


def parse_readings(
    readings: list[str], *, source_file: str | None = None, label: str = "receipt"
) -> Receipt:
    """Parse several independent OCR readings of one image into one receipt.

    Accepted only if >= 2 readings reconcile, all that do agree on every
    amount, and at least two of them agree on the receipt id and timestamp
    (which is then the value used). The first reconciling reading supplies the
    item names -- the caller orders readings best-first. A single reading can
    be wrong in a way that still reconciles (see the module docstring), so a
    lone success is not enough.
    """
    parsed: list[Receipt] = []
    errors: list[str] = []
    for text in readings:
        try:
            parsed.append(parse_text(text, source_file=source_file, label=label))
        except ValueError as exc:
            errors.append(str(exc))
    if len({_amounts(r) for r in parsed}) > 1:
        raise ValueError(
            f"{label}: OCR readings disagree on the amounts ({len(parsed)} of "
            f"{len(readings)} reconciled but not to the same receipt); not stored."
        )
    if len(parsed) < 2:
        detail = errors[0] if errors else "no reading produced text"
        raise ValueError(
            f"{label}: only {len(parsed)} of {len(readings)} OCR readings reconciled, "
            f"need at least 2 that agree. First failure: {detail}"
        )
    identity, votes = Counter((r.receipt_id, r.purchased_at) for r in parsed).most_common(1)[0]
    if votes < 2:
        raise ValueError(
            f"{label}: OCR readings disagree on the receipt id/date; not stored."
        )
    return parsed[0].model_copy(update={"receipt_id": identity[0], "purchased_at": identity[1]})
