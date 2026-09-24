"""HTTP server exposing receipt ingestion over the network.

Backs the Web Upload ingestion path (see CONTEXT.md): unlike Folder Watch,
which only works on a Mac with iCloud Drive, this lets any browser --
Docker/NAS deployments included -- drop a receipt PDF in and have it parsed
and stored.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import export as export_mod
from .grocy import create_product_and_map_and_push, push_receipt, resolve_mapping_and_push
from .grocy_client import GrocyClient
from .grocy_store import GrocyProductDefaults, GrocyStore, push_readiness
from .parse_pdf import parse_pdf
from .store import ReceiptStore


class _MappingResolution(BaseModel):
    raw_name: str
    grocy_product_id: int | None = None
    new_product_name: str | None = None
    skipped: bool = False


def create_app(
    store: ReceiptStore,
    web_dir: Path,
    grocy_store: GrocyStore | None = None,
    grocy_client: GrocyClient | None = None,
) -> FastAPI:
    app = FastAPI()
    uploads_dir = store.data_dir / "uploads"
    grocy_store = grocy_store or GrocyStore(data_dir=store.data_dir)
    if grocy_client is None:
        grocy_client = GrocyClient.from_env()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> dict[str, str]:
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(422, detail="Only PDF files are supported.")

        # Persisted under a unique name (not the original filename) so two
        # uploads that happen to share a name never collide; parse_pdf reads
        # from this final path, so `Receipt.source_file` points here for
        # good, matching what export_web_data expects downstream.
        uploads_dir.mkdir(parents=True, exist_ok=True)
        dest = uploads_dir / f"{uuid.uuid4().hex[:8]}-{Path(file.filename).name}"
        dest.write_bytes(await file.read())

        try:
            receipt = parse_pdf(dest)
        except Exception as exc:
            raise HTTPException(422, detail=str(exc)) from exc

        if not store.save(receipt):
            return {"status": "duplicate", "receipt_id": receipt.receipt_id}

        export_mod.export_web_data(store.all(), web_dir)

        return {
            "status": "added",
            "receipt_id": receipt.receipt_id,
            "total": str(receipt.total),
            "currency": receipt.currency,
        }

    def _require_grocy() -> None:
        if grocy_client is None:
            raise HTTPException(503, detail="Grocy not configured (set GROCY_URL and GROCY_API_KEY).")

    @app.get("/api/grocy/pending")
    def grocy_pending() -> list[dict]:
        mappings = grocy_store.all_mappings()
        result = []
        for r in store.all():
            push_state = grocy_store.get_push_state(r.receipt_id)
            unresolved = [
                {"index": i, "name": li.name}
                for i, li in enumerate(r.line_items)
                if li.name not in mappings
            ]
            failed = [
                {"index": i, "name": r.line_items[i].name, "error": s.error}
                for i, s in push_state.items()
                if s.status == "failed"
            ]
            if unresolved or failed:
                result.append({
                    "receipt_id": r.receipt_id,
                    "purchased_at": r.purchased_at.isoformat(),
                    "store_name": r.store.name,
                    "total": str(r.total),
                    "currency": r.currency,
                    "unresolved": unresolved,
                    "failed": failed,
                })
        return result

    @app.get("/api/grocy/search")
    def grocy_search(q: str) -> list[dict]:
        _require_grocy()
        return [p.model_dump() for p in grocy_client.search_products(q)]

    @app.post("/api/grocy/mappings")
    def grocy_resolve_mapping(body: _MappingResolution) -> dict:
        _require_grocy()
        receipts = store.all()
        if body.new_product_name:
            try:
                pushed = create_product_and_map_and_push(
                    body.raw_name, body.new_product_name, receipts, grocy_store, grocy_client,
                )
            except ValueError as exc:
                raise HTTPException(409, detail=str(exc)) from exc
        else:
            pushed = resolve_mapping_and_push(
                body.raw_name, receipts, grocy_store, grocy_client,
                grocy_product_id=body.grocy_product_id, skipped=body.skipped,
            )
        return {"pushed_receipt_ids": pushed}

    @app.post("/api/grocy/receipts/{receipt_id}/retry")
    def grocy_retry(receipt_id: str) -> dict:
        _require_grocy()
        if not store.has(receipt_id):
            raise HTTPException(404, detail="Receipt not found.")
        receipt = store.load(receipt_id)
        if not push_readiness(receipt, grocy_store.all_mappings()):
            raise HTTPException(409, detail="Receipt is not fully mapped yet.")
        all_ok = push_receipt(receipt, grocy_store, grocy_client)
        return {"all_pushed": all_ok}

    @app.get("/api/grocy/settings")
    def grocy_get_settings() -> dict:
        defaults = grocy_store.get_defaults()
        connected = grocy_client is not None and grocy_client.check_connection()
        locations = grocy_client.list_locations() if grocy_client else []
        quantity_units = grocy_client.list_quantity_units() if grocy_client else []
        return {
            "connected": connected,
            "defaults": defaults.model_dump() if defaults else None,
            "locations": [loc.model_dump() for loc in locations],
            "quantity_units": [q.model_dump() for q in quantity_units],
        }

    @app.put("/api/grocy/settings")
    def grocy_put_settings(body: GrocyProductDefaults) -> dict:
        grocy_store.set_defaults(body)
        return {"status": "ok"}

    public_dir = web_dir / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=public_dir, html=True), name="static")

    return app
