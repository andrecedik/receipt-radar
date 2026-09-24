# receipt-radar

Turn your Kaufland digital receipts (Digitale Kassenbons) and REWE eBons
into structured, self-hosted data. Find out whether that "sale" sticker is actually a better
price than what you already paid.

> Unofficial and unaffiliated with Kaufland. This is a personal-data /
> interoperability tool: it only ever handles your own receipts, and the
> active ingestion path parses PDFs *you* export from the app. It never
> talks to Kaufland's servers.

## What it does

- **Item Price History**: press <kbd>⌘K</kbd>, type any item name, and see
  every price you've paid for it across every receipt: a sparkline plus a
  date-ordered table. Yesterday's "amazed, I didn't know I could do that"
  reaction to your own price history is the whole point.
- **Price Integrity Check**: compares a discounted item's price against the
  median of what *you* actually paid for it before, at the same store, and
  tells you whether the "sale" is real.
- **Total Spend Aggregation**: a running total of what you actually spend,
  rolled up across every store you've fed receipts from.
- **Grocy Stock Push**: one click pushes a receipt's line items into your
  [Grocy](https://grocy.info/) stock, so scanning receipts becomes how your
  pantry inventory stays current.
- Runs entirely on hardware you control. Nothing is uploaded anywhere.

## Why self-hosted

There's no hosted version, and there isn't going to be one anytime soon.
That's deliberate, not a missing feature. Grocery receipts are sensitive-ish
personal data (what you buy, when, how much you spend), and the only way to
avoid becoming a data processor for other people's spending habits is to
never hold their data at all. You run it, your receipts stay on your
infrastructure, full stop.

## Quick Start

For a NAS or any Docker host:

```sh
git clone https://github.com/andrecedik/receipt-radar.git
cd receipt-radar
docker compose up -d
```

This pulls the published multi-arch image (`linux/amd64` / `linux/arm64`)
from GHCR — no build step, no cloning half a toolchain. Open
`http://<host>:8000`, upload a receipt PDF, done.

⚠️ There's no authentication in front of the upload endpoint yet. Keep it on
a trusted network, or put a reverse proxy with auth in front of it before
exposing it beyond `127.0.0.1`.

See [Self-hosting details](#self-hosting-details) below for volumes, Grocy
env vars, and building locally instead of pulling.

## Features

### Item Price History

Every item you've ever bought gets its own page: a sparkline of price over
time and a table of every observation (date, store, price). Reached via the
global <kbd>⌘K</kbd> quick-jump, which searches receipts, items, and pages
at once.

### Price Integrity Check

Kaufland (like most grocers) prints a "sale" price without telling you what
you paid last time. This compares an item's current effective price (after
any discount) against the median of your own prior purchases of that item at
that store — using only your own history, no external price database — and
tells you whether it's a genuine reduction or a sale in name only.

### Total Spend Aggregation

Every parsed receipt rolls up into a running spend total across every store
you've fed receipts from. Kaufland and REWE today (see
[What it can't do yet](#what-it-cant-do-yet)); the data model and UI
don't assume a single retailer.

### Grocy Stock Push

Set `GROCY_URL` and `GROCY_API_KEY`, then push a receipt straight into your
Grocy stock from the **Grocy** tab. The first time a line item shows up, you
resolve it once: match it to an existing Grocy product, create a new one,
or mark it as permanently skipped (for things that never belong in stock,
like loyalty discounts or Pfand/Leergut deposit returns). Every future
receipt with that exact item name resolves itself automatically after that.
A receipt pushes to Grocy only once every one of its line items has been
resolved.

## What it can't do yet

- **Kaufland and REWE only, German only.** The parsers handle the printed
  formats of the Kaufland digital receipt and the REWE eBon (the
  `stationary-ebon-<uuid>.pdf` download from the REWE app or rewe.de); no
  other retailer is supported yet, and both chains only issue these in
  Germany.
- **Only Kaufland receipts from July 2024 onward can be parsed.** Older ones are
  exported by the Kaufland app as its rendered "Receipt Copy" screen, an
  image-only PDF with no text layer (the switch happened in the second half
  of June 2024). The parser is text-based (no OCR), so it rejects those with
  a clear error instead of guessing.
- **No automatic sync.** The Kaufland app has no public API, and the host
  that serves digital receipts is certificate-pinned. Receipts have to be
  exported as PDFs by hand (from the app, or via the browser Upload page)
  rather than pulled automatically. See [Roadmap](#roadmap).
- **No authentication on the upload endpoint.** Fine on a trusted local
  network; not fine exposed to the open internet without a reverse proxy in
  front of it.
- **No Home Assistant integration yet.** Grocy Stock Push exists; an HA
  notification hook ("this item you track just went on genuine sale") does
  not, yet.
- **Single-user, single-household.** There's no concept of accounts, teams,
  or multi-tenant anything. It's built to run one instance for one person's
  own receipts.
- **No cross-retailer price comparison.** Price Integrity Check only ever
  compares an item against *your own* purchase history. It can't tell you
  whether Edeka down the street is cheaper today. See the bigger vision
  below for why, and why that's a deliberate sequencing choice, not an
  oversight.

## Roadmap

**Coming to this repo** (still self-hosted, still yours to run):

- Automated receipt sync: no more manual PDF export, once the app's
  certificate pinning is worked around
- Home Assistant notification hook for genuine-discount alerts
- Parsers for other German grocers (Edeka, Lidl), extending Total Spend
  Aggregation beyond Kaufland and REWE

**The bigger vision** — and honestly, the reason this project exists at all:
grocery prices vary by store and region in ways no single shopper can see on
their own. Cross-retailer price comparison and sale-timing prediction only
become possible with data from many shoppers across many locations. That's a
crowd-data problem a single self-hosted instance structurally can't solve.
It's a different, opt-in system, not a feature that will show up in
`docker compose up`, and it depends on Total Spend Aggregation actually
being useful to people first. If Price Integrity Check earns its keep for
you, that's the bet this whole project is built on.

## Self-hosting details

### Volumes

```yaml
volumes:
  - ./data:/data   # receipts + uploaded PDFs — persists on the host
```

This mount is required. Without it, the container still runs, but everything
lands on its writable layer instead and is lost the moment the container is
removed.

### Grocy Stock Push env vars

Set in a git-ignored `.env` file next to `docker-compose.yml` (loaded
automatically). Never hardcode these:

```sh
GROCY_URL=https://your-grocy-instance
GROCY_API_KEY=your-grocy-api-key
```

Optional. Leave unset and the app runs fine, `/api/grocy/*` just returns
503.

### Building locally instead of pulling

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Or a one-off image for a specific platform:

```sh
docker build --platform linux/amd64 -t receipt-radar .   # or linux/arm64
```

### Alternative ingestion: Folder Watch (macOS + iCloud only)

If you're running this on a Mac with iCloud Drive instead of a NAS, receipts
can be picked up automatically from a local folder instead of uploaded
through the browser:

1. In the Kaufland app: **Digitale Kassenbons → open a receipt → als PDF
   speichern → share to iCloud Drive**, into a folder named
   `digital-receipts`.
2. On the Mac:

   ```sh
   uv run receipt-radar watch            # check once, ingest new PDFs, exit
   uv run receipt-radar watch --no-once  # or keep polling in the background
   ```

Receipts are cached as one JSON file each under
`~/.local/share/receipt-radar/receipts/`. Ingestion is idempotent: safe
to re-run `watch`/`ingest` freely.

## Development

```sh
uv sync
uv run pytest      # core logic; no PDF or network needed

cd web
npm install
npm run dev        # http://localhost:5173, proxies /api to a local `receipt-radar serve`
npm run test
npm run lint
```

Running without Docker, the CLI itself covers everything the web UI does
and more. Run `uv run receipt-radar --help` for the full list, or:

```sh
uv run receipt-radar ingest ~/path/to/a-receipt.pdf
uv run receipt-radar list                       # what's stored
uv run receipt-radar export --format csv -o export.csv
uv run receipt-radar summary                    # monthly rollup (Markdown)
uv run receipt-radar serve --web-dir web        # local API server, for `npm run dev` above
```

See [`web/README.md`](web/README.md) for the frontend build/preview flow in
more detail.

## License

[AGPL-3.0](LICENSE). If you build on this for something that needs a
different license, get in touch.
