# CWNG #908 DEFAULT-theme pilot — final evidence

Date: 2026-07-15
Origin: `http://127.0.0.1:8101` only
Viewport: `1280x800`

## Scope and attachment

- OBSERVED: The browser actions ran through the existing Playwright/CDP browser context.
- ASSUMED: The existing context was the operator-specified Chrome CDP endpoint `9234`; no browser was launched or closed, and the endpoint itself was not independently inspected.
- OBSERVED: No screenshot was taken; no PNG was created.
- OBSERVED: No code, Docker, GitHub, or repository files were changed other than this report.

## Required recovery and baseline gate

- OBSERVED: In one `browser_run_code` action, `context.clearCookies({ domain: '127.0.0.1' })` was followed immediately by `page.goto('http://127.0.0.1:8101/login')`.
- OBSERVED: Baseline URL was `http://127.0.0.1:8101/login`.
- OBSERVED: Classic login form was visible.
- OBSERVED: `body.className` was `login ` and did not contain a token-blur class/token.
- OBSERVED computed body styles:
  - `backgroundColor`: `rgb(242, 242, 242)`
  - `backgroundImage`: `none`
  - `filter`: `none`
  - `backdropFilter`: `none`

## Flow

- OBSERVED: Signed in through the Classic form as `admin`.
- OBSERVED: Classic page exposed the visible `Switch to New UI` link; it was clicked.
- OBSERVED: New UI loaded at `http://127.0.0.1:8101/app/`.
- OBSERVED: Opened `Account: admin`; the visible `Sign out` button was present and clicked.

## Final state

- OBSERVED: Final URL is `http://127.0.0.1:8101/app/`.
- OBSERVED: Final title is `Sign in · Calibre-Web NextGen`.
- OBSERVED: New UI sign-in heading, username/password fields, Remember me checkbox, and Sign in button are visible.
- OBSERVED: `GET /api/v1/auth/me` with same-origin credentials returned HTTP `401` (`ok: false`).

## Cookies (names and attributes only; values intentionally omitted)

- OBSERVED: `cwng_prefer_spa`; domain `127.0.0.1`; path `/`; `httpOnly=false`; `secure=false`; `sameSite=Lax`; persistent expiry present. **Survives final sign-out.**
- OBSERVED: `session`; domain `127.0.0.1`; path `/`; `httpOnly=true`; `secure=false`; `sameSite=Lax`; persistent expiry present.
- OBSERVED: `remember_token` is absent.

## Console

- OBSERVED: 2 errors, 0 warnings were reported by the browser console collector.
- OBSERVED: Errors were `401 (UNAUTHORIZED)` resource failures for `/api/v1/auth/me`.
- OBSERVED: The login page also emitted the browser advisory that password inputs should have an `autocomplete="current-password"` attribute.

Pilot stopped at the requested final state.
