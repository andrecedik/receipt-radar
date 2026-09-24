# Rename kaufland-receipts to receipt-radar

**Status:** accepted

`docs/adr/0006-retailer-breadth-is-the-conversion-lever.md` named the rename "no longer a deferred question" and left the actual name to be chosen in its own ADR. This is that ADR.

We decided: **receipt-radar**. The name had to be retailer-agnostic (fixing the exact bounce signal ADR 0006 identified: a reader who doesn't shop at Kaufland sees the name and correctly concludes it isn't for them) and legible without translation to the actual target audience — r/selfhosted, r/homeassistant, r/grocy skew English-reading even though the retailers currently supported are German. That legibility bar is also what eliminated the two other directions explored. Anything built on "vault" (`receipt-vault`, `Bonvault`) implied secure custody of the receipts as the value proposition, when the product's actual payoff is derived insight — price history, integrity checks, spend totals — not holding onto the documents themselves. And "Kassenbon" (the literal German word for a receipt), while short and immediately legible to the German-speaking subset of the audience, reproduces a milder version of the exact legibility problem this rename exists to fix, for the majority who don't read German.

`receipt-radar` keeps "receipt" — honest, retailer-agnostic description of the input — and pairs it with "radar" for the payoff: ongoing detection of price movement and of a genuine vs. fake sale, not a one-time read of a document. A search for existing projects under the name turned up nothing in the same problem space, just one unrelated repo that happens to share the string. That's a meaningfully cleaner result than the alternative "input + payoff" word tried for the same shape: "lens" collides repeatedly no matter which input word it's paired with, including with an established commercial app.

## Considered Options

- **Kassenbon** — literal German word for a receipt; short, and immediately legible to the German-speaking subset of the audience the current retailers are built for. Rejected: reproduces a milder version of the exact bounce-on-the-name problem this ADR exists to fix, for the English-reading majority of r/selfhosted / r/homeassistant / r/grocy who don't read German.
- **receipt-vault / Bonvault** — "vault" borrows a familiar self-hosted naming pattern (Vaultwarden, Bitwarden). Rejected: implies the value is secure custody of the receipts, but the product's actual payoff is derived insight (price history, integrity checks, spend totals) — storage isn't what anyone is asked to trust it with.
- **pantry-ledger / grocery-ledger / spend-ledger / price-ledger** — plain, descriptive, no translation required. Rejected: "ledger" reads as passive bookkeeping and undersells the headline feature — `CONTEXT.md`'s own title for the project is "Price Intelligence" — and "pantry" specifically over-indexes on Grocy Stock Push, a secondary MVP feature, ahead of the headline Price Integrity Check / Item Price History.
- **receipt-lens / price-lens / spend-lens / pantry-lens** — same "input + insight" shape as the accepted name, and the closest runner-up. Rejected: every pairing collides with an existing project in the same problem space, including a long-established commercial app (Receipt Lens, on the App Store since 2018, receiptlens.com) and several actively developed OSS lookalikes (a receipt-photo-to-spend-tracking tool under `spend-lens`, a supermarket price tracker under `price-lens`). "-lens" is a saturated pattern here, not an available one.

## Consequences

- The rename itself — repo, `pyproject.toml`'s `name` and `[project.scripts]`, the `src/kaufland_receipts` package, `docker-compose*.yml`'s service name and the `ghcr.io/andrecedik/kaufland-receipts` image path, README/CONTEXT.md title — is not done by this ADR. Tracked as separate follow-up work.
- Availability was spot-checked (GitHub, general web search) and came back clean, but no formal trademark or domain-registration check was run. Do that before the name goes anywhere public-facing: the GHCR image push, a registered domain, a PyPI publish if one is ever planned.
- The CLI's current two entry points (`kaufland`, `kaufland-receipts`) become `receipt-radar` — whether to also keep a short alias is worth deciding at rename time, not here.
- "RR" reads cleanly as a mark/logo shorthand, which was part of why this name won over the ledger family. No design work follows from that in this ADR.
