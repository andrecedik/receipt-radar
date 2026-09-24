"""Command-line entrypoint: ``receipt-radar <command>``."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer
import uvicorn

from . import export as export_mod
from .parse_pdf import parse_pdf
from .server import create_app
from .store import ReceiptStore
from .watch import DEFAULT_WATCH_DIR, scan_once

app = typer.Typer(
    add_completion=False,
    help="Pull your own grocery digital receipts into local structured data.",
)


def _store() -> ReceiptStore:
    return ReceiptStore()


@app.command()
def ingest(
    path: Path = typer.Argument(..., help="A receipt PDF, or a folder of them."),
    overwrite: bool = typer.Option(
        False, help="Reparse and replace receipts already in the store (e.g. after a parser update)."
    ),
):
    """Parse one PDF (or every PDF in a folder) into the local store."""
    store = _store()
    pdfs = sorted(path.glob("*.pdf")) if path.is_dir() else [path]
    added = skipped = failed = 0
    for pdf in pdfs:
        try:
            receipt = parse_pdf(pdf)
        except Exception as exc:
            typer.secho(f"  ✗ {pdf.name}: {exc}", fg=typer.colors.RED)
            failed += 1
            continue
        if store.save(receipt, overwrite=overwrite):
            reconciled = "✓" if receipt.totals_match() else "⚠ totals mismatch"
            typer.echo(f"  + {receipt.receipt_id}  ({receipt.total} EUR)  {reconciled}")
            added += 1
        else:
            skipped += 1
    typer.secho(
        f"Done: {added} added, {skipped} already known, {failed} failed.",
        fg=typer.colors.GREEN,
    )


@app.command()
def watch(
    folder: Path = typer.Option(DEFAULT_WATCH_DIR, help="iCloud folder to watch."),
    interval: float = typer.Option(30.0, help="Seconds between scans (only with --no-once)."),
    once: bool = typer.Option(
        True, help="Scan a single time and exit. Pass --no-once to poll continuously."
    ),
):
    """Ingest new receipt PDFs from an iCloud folder.

    By default this checks the folder once, ingests anything new, and exits —
    run it whenever you've shared a receipt. Pass --no-once to keep it running
    and polling in the background instead.
    """
    store = _store()
    if not folder.exists():
        typer.secho(f"Watch folder does not exist yet: {folder}", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    if not once:
        typer.echo(f"Watching {folder} (every {interval:.0f}s, Ctrl+C to stop)")
    while True:
        seen = len(list(folder.glob("*.pdf")))
        added, errors = scan_once(store, folder)
        for name, msg in errors:
            typer.secho(f"  ✗ {name}: {msg}", fg=typer.colors.RED)
        for rid in added:
            typer.secho(f"  + {rid}", fg=typer.colors.GREEN)
        if not added and not errors:
            stamp = f"[{time.strftime('%H:%M:%S')}] " if not once else ""
            typer.echo(f"{stamp}scanned {seen} PDF(s) in {folder.name}, nothing new")
        if once:
            break
        time.sleep(interval)


@app.command(name="list")
def list_receipts():
    """List stored receipts, oldest first."""
    for r in _store().all():
        typer.echo(
            f"{r.purchased_at:%Y-%m-%d %H:%M}  {r.total:>8} {r.currency}  "
            f"{len(r.line_items):>3} items  {r.receipt_id}"
        )


@app.command()
def export(
    format: str = typer.Option("csv", help="csv | json"),
    output: Optional[Path] = typer.Option(None, help="Write to file instead of stdout."),
):
    """Export all stored receipts for spending analysis."""
    receipts = _store().all()
    if format == "csv":
        text = export_mod.to_csv(receipts)
    elif format == "json":
        text = export_mod.to_json(receipts)
    else:
        typer.secho("format must be 'csv' or 'json'", fg=typer.colors.RED)
        raise typer.Exit(2)
    if output:
        output.write_text(text, encoding="utf-8")
        typer.secho(f"Wrote {output}", fg=typer.colors.GREEN)
    else:
        typer.echo(text)


@app.command()
def prices(output: Path = typer.Option(Path("prices.jsonl"), help="JSONL log to append to.")):
    """Append per-item price observations to a price-history log."""
    written = export_mod.append_price_history(_store().all(), output)
    typer.secho(f"Appended {written} new observations to {output}", fg=typer.colors.GREEN)


@app.command()
def summary():
    """Print a monthly spending rollup (Markdown) for the me-brain vault."""
    typer.echo(export_mod.monthly_summary_markdown(_store().all()))


def _store_fingerprint(store: ReceiptStore) -> frozenset[tuple[str, float]]:
    """Cheap change signal for --watch: (filename, mtime) of every stored receipt."""
    return frozenset((p.name, p.stat().st_mtime) for p in store.receipts_dir.glob("*.json"))


@app.command(name="web-data")
def web_data(
    web_dir: Path = typer.Option(Path("web"), help="Path to the web/ (shadcn variant) project."),
    watch: bool = typer.Option(
        False, "--watch",
        help="Keep running and re-export whenever a receipt is added, instead of exiting after one export.",
    ),
    interval: float = typer.Option(3.0, help="Seconds between store checks (only with --watch)."),
):
    """Prepare data for the shadcn/React variant: export receipts.json into
    web/public/data/ and copy source PDFs into web/public/pdfs/.

    Pair this with `npm run dev` (not `build`) inside web/ for local
    development: Vite serves public/ live, so a re-export already shows up
    on the next browser refresh with no rebuild step. Add --watch and even
    the re-export happens on its own as you ingest receipts -- run
    `receipt-radar web-data --watch` and `npm run dev` side by side and a
    refresh always shows the latest data.
    """
    store = _store()
    data_dir = web_dir / "public" / "data"
    pdfs_dir = web_dir / "public" / "pdfs"

    def do_export() -> None:
        count, copied = export_mod.export_web_data(store.all(), web_dir)
        typer.secho(
            f"Wrote {count} receipt(s) to {data_dir / 'receipts.json'}, copied {copied} PDF(s) to {pdfs_dir}",
            fg=typer.colors.GREEN,
        )

    do_export()
    if not watch:
        return

    typer.echo(f"Watching the receipt store for changes (every {interval:.0f}s, Ctrl+C to stop)")
    fingerprint = _store_fingerprint(store)
    while True:
        time.sleep(interval)
        current = _store_fingerprint(store)
        if current != fingerprint:
            fingerprint = current
            do_export()


@app.command()
def serve(
    web_dir: Path = typer.Option(Path("web"), help="Path to the web/ (shadcn variant) project."),
    host: str = typer.Option("127.0.0.1", help="Host to bind."),
    port: int = typer.Option(8000, help="Port to bind."),
):
    """Run the Web Upload server: accepts receipt PDFs at POST /api/upload
    and re-exports web/public/data/receipts.json after each one, plus once
    at startup so existing data shows up immediately after a restart.
    """
    store = _store()
    fastapi_app = create_app(store, web_dir)
    export_mod.export_web_data(store.all(), web_dir)
    uvicorn.run(fastapi_app, host=host, port=port)


if __name__ == "__main__":
    app()
