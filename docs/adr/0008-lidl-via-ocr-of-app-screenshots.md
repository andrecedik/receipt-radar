# Lidl is read by OCR from the app's screenshot export; the Lidl Plus API comes second

**Status:** accepted

ADR 0006 made a **Retailer Parser** over a receipt the user exports themselves the base for every retailer, with a vendor API layered on later as an accelerator. For Lidl that premise does not hold as written: the Lidl app has no PDF or text export. "Export receipt" scrolls the in-app receipt, screenshots it, stitches the screenshots into one tall PNG (1179px wide, 4,000-7,900px high) and shares that. There is no text layer anywhere to parse.

We decided the durable Lidl base is therefore **OCR of that PNG** (`ocr.py` + `parse_lidl.py`), not a PDF parser. ADR 0006's ordering principle still holds and is what this follows: the base is something the user already holds and nobody can withdraw; the **Lidl Plus API** is added afterwards as an optional **Receipt Source**.

ADR 0006 rejected OCR only for legacy Kaufland PDFs and the paper-only Aldi ask, where it was "a large, low-confidence cost serving the smallest ask". That reasoning does not transfer: here the input is a clean digital render in a fixed monospace font, it is the only no-credentials route, and Lidl is one of the three retailers the funnel reading is waiting on.

## OCR is lossy, so a reading is never trusted on its own

Tesseract reliably misreads the receipt font's slashed zero as `8` or `6` (`-0,04` -> `-8,84`), sometimes appends a stray digit (`1,708`) or an extra integer digit (`-08,58`), and garbles the Filiale/Bon digits. A misread price stored silently would poison Item Price History and Price Integrity Check, so:

- The parser repairs only one-directional confusable digits (`8`/`6` -> `0`), cheapest solution first, only when exactly one solution exists, using the receipt's own redundancy: `unit x qty = total`, `kg x EUR/kg = total`, the per-tax-class gross amounts in the `MWST%` table, the grand total, and the printed `Gesamter Preisvorteil` (= sum of discounts).
- **Repair can manufacture a match**, which is the reason for the last check and for the next rule. Observed on real receipts: a misread `1,09` and a misread discount `-0,04` -> `-0,84` cancelled exactly; a dropped `-0,60` discount line was "explained" by rewriting `1,69` -> `1,09`. Digit errors in this font come in multiples of 0.08 / 0.80 / 8, so cancellations are common, not exotic.
- Several independent Tesseract readings are taken (language packs x page-segmentation modes). A receipt is stored only if **at least two readings reconcile and every reconciling reading agrees on every amount**. Receipt id and timestamp are voted across readings (one reading read Filiale `3903` as `39083`). Majority voting was tried and rejected: on one sample the wrong readings outnumbered the right one 3:2.
- A receipt that fails any of this is **rejected with a reason, not stored**. On the seven real screenshots available at the time (2026-09/10) all seven parse; that is a small sample, and rejection is the designed failure mode for the rest.

## Consequences

- **Tesseract is a system dependency** of image ingestion only. The Docker image bundles it with the German and English packs. Native installs need `tesseract` (+ `deu`); without it PDF ingestion is unaffected and an image upload fails with an install hint.
- **Other countries' language packs are not bundled.** `RECEIPT_RADAR_OCR_LANGS` selects any pack installed in the tessdata directory (a manual "download your language pack" path); a missing pack just drops those readings. Whether packs become a hosted/paid convenience later is a Monetization Path question, not decided here. Note the digit-confusion rules were derived from German Lidl receipts only; ADR 0006's warning stands that international Lidl is unproven until a non-DE receipt parses.
- Item *names* are the weakest field (umlauts and punctuation differ by language pack, and e.g. `750g` can read `758g`); prices, quantities and dates are the checked ones. Product Mapping keys on names, so a noisy name may need a second mapping.
- Web Upload, `ingest` and Folder Watch now accept PNG/JPEG as well as PDF. The Receipts page serves the original only for PDFs.
- **The Lidl Plus API remains the planned accelerator**, not started. `lidl-plus` (MIT) documents the endpoints and the item JSON (incl. `codeInput` barcodes, per-item discounts, deposit), but it is unmaintained (last release 2024-02, open issues "auth not working" through 2026-06, and a 2024 report that tickets moved to an HTML "v3" format). Its first step is capturing one real response from a real account; the no-probing rule from ADR 0006 applies.
