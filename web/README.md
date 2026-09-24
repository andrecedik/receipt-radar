# web/ — the site (shadcn/React)

The frontend for browsing receipts, built with [shadcn/ui](https://ui.shadcn.com/)
(Radix + Tailwind CSS v4) on Vite + React + TypeScript. Four pages (receipt
list, receipt detail, item price history, statistics), output to `../site/`.

Needs Node `^20.19.0 || >=22.12.0` (Vite 8's bundler, Rolldown, needs
`node:util`'s `styleText` export, missing before Node 20.12). If you use
nvm, `cd web && nvm use` picks up the pinned version in `.nvmrc`
automatically. `npm run build`/`dev` check this themselves and fail with a
clear message on an incompatible Node — otherwise the failure mode is a
cryptic crash deep in `node_modules/rolldown` ("does not provide an export
named 'styleText'"), which is what an old Node version left active in a
shell (e.g. via nvm) looks like.

## Local development (recommended)

`npm run dev` starts Vite's own dev server with hot-reload on every code
change — no build step at all. Data isn't bundled either (see "Design
notes" below), so it's read straight from `public/data/receipts.json`
on every page load; a browser refresh always shows whatever was last
exported there, still with zero rebuild.

```sh
# from the repo root, in one terminal — exports receipts.json + PDFs once,
# then keeps re-exporting as you ingest new receipts (Ctrl+C to stop)
uv run receipt-radar web-data --watch

cd web            # in another terminal
nvm use           # if you use nvm — matches .nvmrc
npm install       # first time only
npm run dev
# open the URL it prints (usually http://localhost:5173)
```

Refreshing the browser is still required to see newly-ingested receipts —
Vite's dev server doesn't auto-reload the page just because a file under
`public/` changed (confirmed against Vite 8; it may in other setups, but
don't rely on it). Code edits, on the other hand, hot-reload automatically
with no refresh needed.

## Building

Only needed to produce a static, deployable copy of the site (`../site/`)
— not for local development, see above.

```sh
# from the repo root — exports web/public/data/receipts.json and copies
# source PDFs into web/public/pdfs/ from the local receipt store
uv run receipt-radar web-data

cd web
nvm use       # if you use nvm — matches .nvmrc
npm install   # first time only
npm run build # writes ../site/
```

## Viewing the built site

The build **must be served over HTTP** — it can't be opened directly via
`file://`. Vite's build output uses native ES module
`<script type="module">` tags, and Chrome (and other browsers) refuse to
load those under the `file://` origin (a CORS restriction, not a bug in
this project).

Easiest: Vite's own preview server, run from `web/` right after `npm run
build` — no `cd` to get wrong:

```sh
cd web
npm run preview
# open the URL it prints (usually http://localhost:4173)
```

Or serve the `site/` output directly with anything static — **note the
directory**: it's `site/`, not `web/` (which holds the unbuilt Vite
source; serving *that* also loads without error but renders a blank page,
since a plain static server can't process `web/index.html`'s
`<script type="module" src="/src/main.tsx">` reference):

```sh
cd site
python3 -m http.server 8000
# open http://localhost:8000
```

Routing is client-side (`HashRouter` — URLs look like `#/receipts/<id>`)
specifically so the built site is a single `index.html` with no server-side
route configuration needed; any static file server works.

## Design notes

- **Data**: fetched at runtime from `public/data/receipts.json` (gitignored
  — regenerate with `receipt-radar web-data`), not bundled at build time — kept
  out of the JS bundle so the shipped chunk size doesn't grow with every
  receipt ever ingested, and so new data shows up on refresh without a
  rebuild (see "Local development" above).
- **Theme toggle**: manual light/dark pin, persisted in `localStorage`,
  applied before first paint to avoid a flash — implemented with Tailwind's
  `class` dark-mode strategy (`.dark` on `<html>`).
- **Charts**: a small hand-built SVG sparkline component, no charting
  library dependency.
