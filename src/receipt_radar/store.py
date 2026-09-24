"""Idempotent local cache of receipts.

One JSON file per receipt under ``~/.local/share/receipt-radar/receipts/``,
named by ``receipt_id``. Re-ingesting a receipt that is already stored is a
no-op, so both the PDF watcher and any future API sync can be run repeatedly
without creating duplicates.

Raw receipt JSON lives here — deliberately *outside* any git repo and outside
the Obsidian vault. Only aggregate rollups are meant to leave this directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from .models import Receipt


def default_data_dir() -> Path:
    """Resolve the cache directory, honouring XDG_DATA_HOME."""
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "receipt-radar"


class ReceiptStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.receipts_dir = self.data_dir / "receipts"
        self.receipts_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, receipt_id: str) -> Path:
        # receipt_id is used as a filename; keep it filesystem-safe.
        safe = receipt_id.replace("/", "_").replace(os.sep, "_")
        return self.receipts_dir / f"{safe}.json"

    def has(self, receipt_id: str) -> bool:
        return self._path_for(receipt_id).exists()

    def save(self, receipt: Receipt, *, overwrite: bool = False) -> bool:
        """Persist a receipt. Returns True if written, False if it already existed.

        With ``overwrite=False`` (the default) an existing receipt is left
        untouched — this is what makes repeated syncs idempotent.
        """
        path = self._path_for(receipt.receipt_id)
        if path.exists() and not overwrite:
            return False
        path.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
        return True

    def load(self, receipt_id: str) -> Receipt:
        return Receipt.model_validate_json(self._path_for(receipt_id).read_text("utf-8"))

    def all(self) -> list[Receipt]:
        receipts = [
            Receipt.model_validate_json(p.read_text("utf-8"))
            for p in self.receipts_dir.glob("*.json")
        ]
        return sorted(receipts, key=lambda r: r.purchased_at)
