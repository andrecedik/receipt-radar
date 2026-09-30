"""Entry point for parsing one receipt file, whatever form it is in.

PDFs carry a text layer and go through :func:`parse_pdf.parse_pdf`. Image
receipts (the Lidl app shares a stitched PNG) have no text layer: they are
OCR'd, cross-checked across several readings, and parsed by the retailer's
parser -- currently only Lidl's, see :mod:`parse_lidl`.
"""

from __future__ import annotations

from pathlib import Path

from .models import Receipt
from .ocr import IMAGE_SUFFIXES, ocr_readings
from .parse_lidl import parse_readings
from .parse_pdf import parse_pdf

SUPPORTED_SUFFIXES = {".pdf", *IMAGE_SUFFIXES}


def is_supported(path: Path | str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def parse_file(path: Path) -> Receipt:
    """Parse one receipt PDF or screenshot into a :class:`Receipt`."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix in IMAGE_SUFFIXES:
        return parse_readings(ocr_readings(path), source_file=str(path), label=path.name)
    raise ValueError(f"{path.name}: unsupported file type (PDF, PNG and JPEG are supported)")
