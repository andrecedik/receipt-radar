# Kaufland mobile API — reverse-engineering notes

Captured 2026-08-21 by intercepting the iOS Kaufland app (v6.15) with mitmproxy.
This documents the backend so that a **future auto-sync client** can be built
without re-doing the discovery. It is not yet implemented — the receipts host is
certificate-pinned (see below), so the app in use today is the PDF pipeline.

> Personal-data / interoperability use only: authenticate as yourself, fetch
> your own receipts. Unofficial, unaffiliated with Kaufland.

## Hosts and pinning status

| Host | Role | TLS interceptable? |
|---|---|---|
| `account.kaufland.com` | Identity provider — **cidaas** (Widas CIAM). OAuth2 / OIDC. | Not re-hit during capture (app had a cached session). Likely pinned. |
| `shop-mobile-bff.cloud.kaufland.de` | Marketplace/shop backend-for-frontend | **Yes** — plain JSON seen |
| `app.kaufland.net` | **Loyalty backend — Digitale Kassenbons live here** | **No — certificate-pinned** (18/18 TLS handshakes refused by client) |
| `swasc.kaufland.com` | Adobe Experience Edge analytics | Partially pinned; irrelevant |

**Consequence:** while the proxy is active the app cannot reach `app.kaufland.net`,
so the receipts screen shows *"Your digital receipts can't be loaded."* That error
is caused by interception, not a real outage.

## Authentication (cidaas OAuth2)

The access token is an **RS256 JWT** issued by cidaas. Decoded header/claims from
the captured `POST /auth/login` request to the shop BFF:

```
header: { "alg": "RS256", "kid": "5ec4c180-…", "typ": "at+jwt" }
claims:
  iss:        https://account.kaufland.com
  client_id:  72a21a5f-f5fd-4b0f-a292-3674663e3ac1
  aud:        72a21a5f-f5fd-4b0f-a292-3674663e3ac1
  scope:      profile openid cidaas:register cidaas:users_write phone
              identities groups roles offline_access address email
  roles:      ["USER"]
  exp/iat:    ~24h token lifetime
```

Key facts for a client:
- IdP is **cidaas** — standard OIDC discovery should live at
  `https://account.kaufland.com/.well-known/openid-configuration`.
- `offline_access` is granted ⇒ a **refresh token** exists ⇒ after one
  browser-based login the client can refresh headlessly (the `lidl-plus` model).
- The shop BFF accepts the cidaas access token as `Authorization: Bearer …` and
  exchanges it via `POST /auth/login` for its own session.

## Known shop-BFF endpoints (interceptable, not receipts)

Base: `https://shop-mobile-bff.cloud.kaufland.de`
Required headers observed: `authorization: Bearer <jwt>`, `app-version: 6.15`,
`accept-language: en`, `user-agent: Kaufland - Mobile App - Marketplace - iOS - 6.15 - (…)`

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | body `{access_token, …}`; exchanges cidaas token for shop session |
| GET | `/accounts/self` | returns `hashed_number`, `saldo`, `membershipStatus` |
| GET | `/carts/self` | shop cart summary |

## The missing piece — filled in 2026-09-24, via a real app's flow (not probed)

A r/grocy commenter (`vicegold`) DM'd endpoint details he recovered from the
real Kaufland app, in reply to a request sent 2026-09-23 (see the project hub
in the me-brain vault for the thread). Per the 2026-08-21 rule, this is
accepted as-is — someone else's own app traffic, not a guess — and nothing
here has been probed or verified against a live account yet.

**The receipts backend is not `app.kaufland.net`.** It's a separate host,
`p.crm-dynamics.schwarz` — a Schwarz-Group CRM system, not the loyalty backend
this doc originally targeted. `app.kaufland.net` may still be what the app
itself calls (proxying through), or the pinning capture simply hit the wrong
host for this feature; unresolved either way, and doesn't matter for a client
that talks to `p.crm-dynamics.schwarz` directly.

**Auth**: same cidaas IdP as above, confirmed by a matching `client_id`
(`72a21a5f-f5fd-4b0f-a292-3674663e3ac1` — identical to the one decoded from
the shop-BFF capture, so this is the same public client, not a different app).
Authorization Code + PKCE, no client secret:

| Purpose | Endpoint |
|---|---|
| Authorize | `https://account.kaufland.com/authz-srv/authz` |
| Token | `https://account.kaufland.com/token-srv/token` |

Redirect URI is the iOS app's own scheme (`com.kaufland.iosapp://oauth/callback`),
so this only works headlessly if a client can complete one interactive
browser login and then rely on the `offline_access` refresh token this doc
already confirmed is granted. Vicegold's own note: authorization codes expire
fast — re-run the flow if the exchange comes back invalid/expired.

**Receipts endpoint**:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v2/customers/{username}/transactions` | Query params: `start`, `limit`, `country`, `version`. Headers: `Authorization: Bearer <token>`, `app-platform`, `app-version`, `accept`. |

Response shape, from vicegold's example script — a receipt/transaction has
`id`, `timestamp`, `receiptNumber`, `sum`, `saving`, `currency`, a `store`
object (`id`, `name`, `city`), and a `positions` array. The example script's
own sample only showed `name`, `quantity`, `unitPrice` (cents) per position.
**Unconfirmed discrepancy:** vicegold's earlier r/grocy screenshot (2026-09-22)
showed `gtin`, `taxClassItem` and a top-level `receiptBarcode` that the script
doesn't demonstrate — worth confirming against a real account before relying
on GTINs being present on every line.

### What's still open
- No client has been written or tested against this — accepted as reported,
  not yet verified end-to-end.
- Whether `gtin`/`taxClassItem` are reliably present (see discrepancy above).
- Rate limits / attestation requirements: none observed, none mentioned.
- Per ADR 0006, this is a **Receipt Source accelerator layered on the Kaufland
  parser**, not a replacement, and not on the critical path for REWE/EDEKA/Lidl
  — this section is captured for when it's picked up, not a signal it's next.
