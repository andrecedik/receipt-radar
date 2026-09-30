"""File-type dispatch for ``parse_file``, plus an end-to-end OCR check that
runs only where Tesseract and the (git-ignored, real) Lidl screenshots in
``samples/`` are available."""

import shutil
from pathlib import Path

import pytest

from receipt_radar import parse
from receipt_radar.ocr import ocr_readings

FIXTURE = (Path(__file__).parent / "fixtures" / "lidl_synthetic.txt").read_text("utf-8")
SAMPLES = Path(__file__).parent.parent / "samples"


def test_is_supported():
    assert parse.is_supported("a.pdf") and parse.is_supported("A.PNG") and parse.is_supported("b.jpeg")
    assert not parse.is_supported("notes.txt")


def test_image_goes_through_ocr_readings_and_the_lidl_parser(tmp_path, monkeypatch):
    shot = tmp_path / "PNG image-1.png"
    shot.write_bytes(b"not really a png")
    monkeypatch.setattr(parse, "ocr_readings", lambda path: [FIXTURE, FIXTURE])

    receipt = parse.parse_file(shot)

    assert receipt.store.name == "Lidl"
    assert receipt.source == "image"
    assert receipt.source_file == str(shot)
    assert receipt.totals_match()


def test_unsupported_suffix_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        parse.parse_file(tmp_path / "notes.txt")


def test_missing_tesseract_gives_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Tesseract"):
        ocr_readings(tmp_path / "x.png")


@pytest.mark.skipif(
    shutil.which("tesseract") is None or not list(SAMPLES.glob("PNG image-*.png")),
    reason="needs Tesseract and real Lidl screenshots in samples/",
)
def test_real_lidl_screenshots_parse_and_reconcile():
    for shot in sorted(SAMPLES.glob("PNG image-*.png")):
        receipt = parse.parse_file(shot)
        assert receipt.store.name == "Lidl", shot.name
        assert receipt.totals_match(), shot.name
