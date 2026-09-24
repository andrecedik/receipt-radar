"""Watch an iCloud folder for new receipt PDFs and ingest them.

Workflow: in the Kaufland app, share each receipt PDF into an iCloud Drive
folder (default below). This module scans that folder and feeds any PDF not yet
in the store through :func:`parse_pdf`.

A plain poll loop is used rather than a filesystem-event library: iCloud
materialises files lazily and rewrites their mtimes on sync, so periodic
re-scanning is both simpler and more reliable here. Ingestion is idempotent
(see :class:`ReceiptStore`), so re-scanning the same file costs nothing.
"""

from __future__ import annotations

import time
from pathlib import Path

from .parse_pdf import parse_pdf
from .store import ReceiptStore

DEFAULT_WATCH_DIR = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "digital-receipts"
)


def scan_once(
    store: ReceiptStore, watch_dir: Path
) -> tuple[list[str], list[tuple[str, str]]]:
    """Ingest every not-yet-seen PDF in ``watch_dir``.

    Returns ``(newly_added_ids, errors)`` where errors is a list of
    ``(filename, message)`` for files that failed to parse.
    """
    added: list[str] = []
    errors: list[tuple[str, str]] = []
    for pdf in sorted(watch_dir.glob("*.pdf")):
        try:
            receipt = parse_pdf(pdf)
        except Exception as exc:  # parsing is best-effort per file
            errors.append((pdf.name, str(exc)))
            continue
        if store.save(receipt):
            added.append(receipt.receipt_id)
    return added, errors


def watch_loop(store: ReceiptStore, watch_dir: Path, interval: float = 30.0):
    """Poll ``watch_dir`` forever, yielding a summary after each pass."""
    while True:
        yield scan_once(store, watch_dir)
        time.sleep(interval)
