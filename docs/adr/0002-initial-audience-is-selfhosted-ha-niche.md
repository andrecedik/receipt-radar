# Initial audience is the self-hosted/home-automation niche, not general consumers

**Status:** accepted — the parser-sequencing consequence below is superseded by `docs/adr/0006-retailer-breadth-is-the-conversion-lever.md`; the audience choice, channel sequencing and success metric stand.

We decided the initial target audience for the **Self-Hosted Distribution** MVP is the self-hosted/home-automation community (r/selfhosted, r/homeassistant, r/grocy) rather than general Kaufland shoppers. This audience already runs Docker-based NAS setups, already trusts self-hosted tools with sensitive-ish personal data, and already has a stated need for grocery/spend tracking (it's why Grocy exists) — making it a sharper, more reachable wedge for validating whether **Price Integrity Check** and **Total Spend Aggregation** are worth anything to anyone besides the author.

This choice drives two concrete build priorities ahead of further parser work:

1. **Docker packaging** — this audience evaluates tools by whether there's a container image they can pull into their NAS's Docker UI (Synology/Unraid/TrueNAS/CasaOS/etc.); an `npm install` / `uv run` developer workflow is a hard adoption wall for most of them.
2. **Grocy Stock Push** — for this audience, feeding an existing Grocy stock-tracking setup is the actual hook; Price Integrity Check alone is a nice-to-have to them, not the reason they'd install it. This is a launch-blocking dependency: outreach to this audience should wait until Grocy Stock Push ships. **HA Notification Hook** is not part of this same gate — it can ship after launch without undermining the audience's reason to install, since Grocy Stock Push alone already delivers the "actual hook" value.

## Consequences

- ~~Rewe/Edeka/Lidl multi-retailer parsing (see `CONTEXT.md`'s **Total Spend Aggregation**) is not the next priority~~ **(superseded by ADR 0006 — retailer breadth is now the next priority; both gates below shipped)** — Docker packaging and Grocy/HA integration are, since they're what make the Kaufland-only MVP appealing to the chosen validation audience.
- Renaming the project (currently `kaufland-receipts`) is deferred until this audience bet is validated — see the project's open renaming question.
- Revisit this decision if outreach to these communities doesn't produce real installs/usage — the audience bet itself would need reconsidering, not just the packaging/integration work.
- **Launch channel sequencing:** r/selfhosted and r/grocy are wave one — both audiences are served by what ships at launch (Docker packaging + Grocy Stock Push). r/homeassistant moves to wave two, deferred until HA Notification Hook ships, since launching there without it undersells to an audience that came specifically for HA integration.
- **Geographic/language narrowing:** because the parser only supports Kaufland, and Kaufland doesn't operate outside Germany (unlike Lidl/Aldi), the addressable slice of "self-hosted/home-automation community" is German-speaking self-hosters specifically, not the broader English-first international audience r/selfhosted and r/homeassistant mostly serve. This pushes marketing outreach toward German-specific venues (e.g. homeserverforum.de, the Home Assistant Community Forum's German section, German HA/self-hosted Discords) alongside the subreddits, not instead of them. Considered temporary — expected to widen once Total Spend Aggregation adds non-Kaufland parsers — but with no committed timeline, so treated as a real current constraint rather than an ignorable one.
