"""Turn stored receipts into the artefacts the four use cases consume.

* CSV / JSON dumps for ad-hoc spending analysis.
* A ``prices.jsonl`` price-history log keyed by article, appended over time.
* A monthly spending summary as Markdown for the me-brain vault.

Everything reads from the local :class:`ReceiptStore`; nothing here re-parses
PDFs or hits the network.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .models import Receipt
from .price_integrity import compute_verdicts
from .store import ReceiptStore


def to_csv(receipts: list[Receipt]) -> str:
    """One row per line item — the shape most spreadsheets/BI tools want."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["purchased_at", "receipt_id", "store", "item", "quantity",
         "unit_price", "line_total", "tax_class", "receipt_total", "currency"]
    )
    for r in receipts:
        for li in r.line_items:
            writer.writerow(
                [r.purchased_at.isoformat(), r.receipt_id, r.store.name, li.name,
                 li.quantity, li.unit_price or "", li.total_price,
                 li.tax_class or "", r.total, r.currency]
            )
    return buf.getvalue()


def to_json(receipts: list[Receipt]) -> str:
    return json.dumps([r.model_dump(mode="json") for r in receipts], indent=2)


def append_price_history(receipts: list[Receipt], path: Path) -> int:
    """Append one JSONL observation per line item; returns rows written.

    Deduped by (receipt_id, item name) against what's already logged, so this is
    safe to re-run as new receipts arrive.
    """
    seen: set[tuple[str, str]] = set()
    if path.exists():
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            obs = json.loads(line)
            seen.add((obs["receipt_id"], obs["item"]))

    rows = 0
    with path.open("a", encoding="utf-8") as fh:
        for r in receipts:
            for li in r.line_items:
                # Only log real products with a per-unit price. Skips loyalty
                # discounts and weight-priced lines, which have no unit price and
                # would pollute a per-item price series.
                if li.unit_price is None or li.total_price < 0:
                    continue
                key = (r.receipt_id, li.name)
                if key in seen:
                    continue
                fh.write(json.dumps({
                    "date": r.purchased_at.date().isoformat(),
                    "receipt_id": r.receipt_id,
                    "item": li.name,
                    "article_number": li.article_number,
                    "unit_price": str(li.unit_price) if li.unit_price else None,
                    "quantity": str(li.quantity),
                }, ensure_ascii=False) + "\n")
                seen.add(key)
                rows += 1
    return rows


def export_web_data(receipts: list[Receipt], web_dir: Path) -> tuple[int, int]:
    """Write receipts.json + copy source PDFs for the shadcn/React site (web/).

    receipts.json lives under public/, not src/, so the built app fetches it
    at runtime instead of Vite inlining it into the JS bundle -- otherwise
    the shipped chunk size grows with every receipt ever ingested. Shared by
    the one-shot and ``--watch`` paths of ``receipt-radar web-data``.

    Returns ``(receipts written, PDFs copied)``.
    """
    data_dir = web_dir / "public" / "data"
    pdfs_dir = web_dir / "public" / "pdfs"
    data_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir.mkdir(parents=True, exist_ok=True)

    # `source_file` is the ingest-time path, which can move or be deleted
    # afterwards -- record whether the PDF was actually copied so the UI can
    # tell "no PDF" apart from "PDF used to exist" instead of trusting the
    # stale path string (see kaufland-receipts code review finding #6).
    copied = 0
    records = []
    verdicts = compute_verdicts(receipts)
    for r in receipts:
        record = r.model_dump(mode="json")
        pdf_available = bool(r.source_file and Path(r.source_file).exists())
        record["pdf_available"] = pdf_available
        for index, verdict in verdicts.get(r.receipt_id, {}).items():
            record["line_items"][index]["price_verdict"] = verdict.model_dump(mode="json")
        if pdf_available:
            shutil.copy2(r.source_file, pdfs_dir / f"{r.receipt_id}.pdf")
            copied += 1
        records.append(record)

    (data_dir / "receipts.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return len(receipts), copied


def monthly_summary_markdown(receipts: list[Receipt]) -> str:
    """A compact per-month spending rollup for the me-brain vault."""
    by_month: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    counts: dict[str, int] = defaultdict(int)
    for r in receipts:
        key = r.purchased_at.strftime("%Y-%m")
        by_month[key] += r.total
        counts[key] += 1

    lines = ["| Month | Receipts | Total (EUR) |", "|---|---:|---:|"]
    for month in sorted(by_month):
        lines.append(f"| {month} | {counts[month]} | {by_month[month]:.2f} |")
    grand = sum(by_month.values(), Decimal(0))
    lines.append(f"| **Total** | **{sum(counts.values())}** | **{grand:.2f}** |")
    return "\n".join(lines)
