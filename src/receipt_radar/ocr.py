"""OCR for receipt screenshots, via the ``tesseract`` binary.

Some retailers give no text-layer PDF: the Lidl app's "export" is one tall PNG
stitched from screenshots of the in-app receipt. Tesseract is an *optional*
system dependency (the Docker image bundles it); PDF-only users never need it.

One OCR pass is not trustworthy enough to store prices from (it misreads the
slashed zero of the receipt font), so :func:`ocr_readings` returns several
independent readings -- different language packs and page-segmentation modes --
for the retailer parser to cross-check (see :func:`parse_lidl.parse_readings`).

Language packs are the extension point for other countries. The Docker image
bundles ``deu`` and ``eng``; ``RECEIPT_RADAR_OCR_LANGS`` (comma-separated
Tesseract language specs, best-first, e.g. ``nld,eng,nld+eng``) selects any
other pack installed in Tesseract's tessdata directory. A reading whose pack is
missing is skipped, not fatal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
DEFAULT_LANGS = "deu,eng,deu+eng"
# 6 = one uniform block (a receipt column); 4 = variable-size column. Different
# layout analysis gives readings whose errors are not identical.
PAGE_SEGMENTATION_MODES = (6, 4)


def _read(path: Path, lang: str, psm: int) -> str:
    proc = subprocess.run(
        ["tesseract", str(path), "stdout", "--psm", str(psm), "-l", lang],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300])
    return proc.stdout


def ocr_readings(path: Path) -> list[str]:
    """Independent OCR readings of ``path``, best-first (the first language
    pack supplies item names, so list the receipt's own language first).

    Raises ``RuntimeError`` if Tesseract is missing or no reading succeeded.
    """
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "Reading image receipts needs Tesseract OCR, which is not installed "
            "(macOS: `brew install tesseract tesseract-lang`; Debian/Ubuntu: "
            "`apt install tesseract-ocr tesseract-ocr-deu`). The Docker image includes it."
        )
    langs = [
        s.strip() for s in os.environ.get("RECEIPT_RADAR_OCR_LANGS", DEFAULT_LANGS).split(",")
        if s.strip()
    ]
    jobs = [(lang, psm) for psm in PAGE_SEGMENTATION_MODES for lang in langs]
    results: list[str] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(len(jobs), os.cpu_count() or 2)) as pool:
        futures = [pool.submit(_read, Path(path), lang, psm) for lang, psm in jobs]
        for (lang, psm), fut in zip(jobs, futures):
            try:
                results.append(fut.result())
            except RuntimeError as exc:
                errors.append(f"{lang}/psm{psm}: {exc}")
    if not results:
        raise RuntimeError(
            f"Tesseract produced no reading of {Path(path).name}: {errors[0] if errors else ''} "
            f"(languages tried: {', '.join(langs)})"
        )
    return results
