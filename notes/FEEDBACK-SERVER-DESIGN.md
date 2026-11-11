# Feedback server + "Back to old theme" popup — design (2026-06-30)

All decisions are operator-confirmed. Status: code not yet built; deploy blocked on
two operator-gated auth steps (see Blockers).

## Goal
When a user leaves the new UI ("Back to old theme"), show a popup on the **classic**
page to collect anonymous feedback about the redesign. Maximally anonymous —
"unmarked mail": only the feedback content is sent/stored, nothing identifying.

## Decisions (confirmed)
- **Host:** Cloudflare Worker + D1 (operator's pick; entirely on Cloudflare's edge,
  free tier, IP terminates at Cloudflare, never reaches any of our servers). Railway
  acceptable fallback if a Worker token isn't created (then front it with Cloudflare
  for the same IP-edge privacy).
- **Domain:** `calibrewebnextgen.com` (Porkbun, free WHOIS
  privacy). Available @ **$11.08/yr** (checked 2026-06-30).
  - **apex `calibrewebnextgen.com`** → a WIP homepage (frontend-design, CWNG dark+amber
    look) that just links to the GitHub repo.
  - **`app.calibrewebnextgen.com`** → the feedback endpoint (the Worker).
- **Store-only** (no ntfy). Stored record = `{ type, reasons[], comment, created_at }`
  and NOTHING else.
- **Anonymity:** "Strip everything" — no username, no instance/version, no cookies, no
  fingerprint. Connection IP is unavoidable at the TLS edge; use only a **salted hash
  of it held in memory for the 60s rate-limit window, never written to D1**.
- **Abuse controls:** IP rate-limit **1 per type per minute** (via salted-hash key +
  Cloudflare rate-limit binding / KV TTL); **type whitelist** = `["new_version_feedback"]`
  (first + only type for now); Cloudflare **Turnstile** bot-wall (we have a CF account).
  CORS locked to the CWNG origin(s).
- **TLS:** Cloudflare-managed publicly-trusted cert (NOT self-signed — browsers would
  block self-signed; managed cert is the honest basis for the "encrypted/secure" claim).
- **Encrypted claim:** HTTPS/TLS in transit. (Optional later: app-layer HMAC so the
  server can prove a submission came from our app — not needed; rate-limit+Turnstile
  cover abuse.)

## Popup (classic/old theme, frontend-design, matches caliBlur)
- Trigger: "Back to old theme" in the new UI → on arriving at the classic page, the
  popup appears (gated by a one-shot flag/param set during the switch).
- **Concise, 2 steps** + the optional toggle:
  - Step 1 — "What made you switch back?" reason checkboxes: *a glitch/bug · the look ·
    a missing feature · too different* (multi-select).
  - Step 2 — short free-text comment + **Submit**.
- Always visible: checkbox **"Anonymize my feedback"** (checked by default), and
  centered beneath it (not tied to the checkbox text) a 🔒 line + a short disclaimer
  that lists the protections honestly:
  - "No account, name, IP, version, or device info is sent or stored."
  - "Sent over HTTPS to a server we run; saved as just your feedback — like unmarked
    mail."
  - Honest residual: "The network edge (Cloudflare) momentarily sees your IP to stop
    spam; we only use a one-way hash of it for ~60s and never store it."
- Must respect **lockdown mode** ([[lockdown-mode]] task #61) when that ships — if
  lockdown is on, the popup doesn't send (or isn't offered).

## Worker shape (app.calibrewebnextgen.com)
- `POST /feedback` JSON: `{ type, reasons[], comment, turnstileToken }`.
  - Validate `type ∈ whitelist`; validate Turnstile; rate-limit by salted-hash(IP)+type
    (1/min); cap comment length; insert `{type,reasons,comment,created_at}` into D1.
  - Return 204 (or `{ok:true}`); never echo IP/identity.
- D1 table `feedback(id, type, reasons TEXT json, comment TEXT, created_at)`.
- Operator reads feedback via `wrangler d1 execute` or a tiny protected admin route.

## Blockers (operator-gated)
1. **Domain purchase.** Porkbun API can't register new domains, and the Porkbun web
   login is Pork-Captcha/Turnstile + SMS-2FA gated (operator only). → Operator registers
   `calibrewebnextgen.com` (card on file), OR completes the login so the rest is driven.
2. **Cloudflare Workers/D1 token.** Our only CF token is DNS-scoped (resumestudio.dev).
   Deploying a Worker+D1 needs a token with Account: Workers Scripts:Edit + D1:Edit
   (+ Pages if the homepage goes on Pages). → Operator creates it in the CF dashboard and
   saves it to the vault, OR we host on Railway (token already in vault) fronted by CF.

## Build order (once unblocked)
1. Register domain → add as Cloudflare zone → DNS (apex homepage, `app` → Worker).
2. Worker + D1 (store-only, rate-limit, whitelist, Turnstile, CORS).
3. WIP homepage (apex) — CWNG-styled, links to GitHub.
4. Classic-theme popup + "Back to old theme" trigger; point at app.calibrewebnextgen.com.
5. Verify end-to-end (submit → row in D1; rate-limit; Turnstile; anonymity = no IP/identity stored).

---

## DEPLOYMENT STATUS — LIVE (2026-07-01)

All operator-gated blockers cleared this session; the feature is built, deployed, and verified.

- **Domain:** `calibrewebnextgen.com` — registered at Porkbun (paid via crypto), active Cloudflare zone `f26afa37dee5e7e71af66b392ebdd3ed`, account `877ba3b6cb3a042d73e5da640e24a4f0`.
- **CF token:** in the vault as *"Cloudflare API token (Workers/D1/Pages/DNS — CWNG feedback server)"*. Actual grant is Workers + Zone:Read (NOT Zone:DNS:Edit, NOT D1) — so we used **KV instead of D1** and **worker routes instead of custom domains** (see below).
- **Feedback Worker:** `cwng-feedback`, LIVE at `https://app.calibrewebnextgen.com/feedback` (custom_domain — `app.` had no conflicting record). Store-only into **KV** (D1 abandoned: token lacks D1 perm):
  - `FEEDBACK` KV `d941e82e8d404e8dbdb4f0224b231348` — records keyed `fb:<created_at>:<uuid8>` = exactly `{type,reasons,comment,created_at}`.
  - `RATELIMIT` KV `5d227d602d8d41f3b12e0ba11e594a98` — salted-hash(IP)+type key, TTL 60s, never stores IP. `RL_SALT` secret set.
  - Type whitelist `["new_version_feedback"]`; `MAX_COMMENT=2000`; permissive CORS; `TURNSTILE_SECRET` **unset** → Turnstile is a no-op for now (rate-limit + whitelist cover abuse). Source: `feedback-server/`.
  - Verified anonymous end-to-end: submit → 200 `{ok:true}`, stored record had no IP/identity; test record deleted.
- **Homepage:** `cwng-homepage`, LIVE at `https://calibrewebnextgen.com` + `www` via **worker routes** (`*/*` patterns) that intercept the proxied registrar parking record — no DNS:Edit needed. Source: `homepage/`. WIP splash: CWNG dark+amber, links to GitHub/Discord/Ko-fi + docker pull.
- **Popup:** shipped in **PR #581** (`feat/60-classic-feedback-popup`) — classic-page partial + `cwng-feedback.js` + SPA "Back to the classic view" entry + the CSP `connect-src` fix. Verified live in a container end-to-end.

### Follow-ups (not blocking)
- Optional: create a Turnstile widget + set `TURNSTILE_SECRET` on `cwng-feedback` to enable the bot-wall (the Worker already verifies it when the secret is present).
- Optional cleanup: once a Zone:DNS:Edit token/dashboard is available, delete the apex+www parking records and switch the homepage back to `custom_domain` for a dedicated hostname (cosmetic; routes work fine).
- When lockdown mode (#61) ships, gate the popup partial off when lockdown is on.
- Operator reads feedback: `wrangler kv key list --namespace-id d941e82e8d404e8dbdb4f0224b231348` (or the CF dashboard KV browser), then `kv key get <key>`.
