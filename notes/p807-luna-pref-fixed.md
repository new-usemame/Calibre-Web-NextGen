# CWNG #908 reporter-flow verification

Status: IN PROGRESS

- Scope: existing shared Chrome at `http://127.0.0.1:9234`; app origin limited to `http://127.0.0.1:8101` and same-origin paths.
- Constraints: viewport 1280x800; cookies cleared before start; no cookie values recorded; no PNG.
- Evidence log will label each claim OBSERVED or ASSUMED.

## Initial classic login — OBSERVED

- Existing tab was already at `http://127.0.0.1:8101/login`; viewport set to 1280x800.
- Cookies were cleared before navigation. After the clean login-page response, cookie metadata showed only `session` (`HttpOnly`, `SameSite=Lax`, path `/`, host `127.0.0.1`); value intentionally omitted.
- URL: `/login`; title: `Calibre-Web NextGen | Login`.
- Accessibility surface: Username textbox, Password textbox, checked Remember Me checkbox, Login button.
- DEFAULT-theme proof: `body.className` was `login  blur`; `body.classList.contains('caliBlur')` was false; computed body `filter`, `backdrop-filter`, and `transform` were all `none`. The `blur` class is therefore not the `caliBlur` body class under test.

## Classic sign-in and New UI — OBSERVED

- `admin` / `admin123` accepted; login redirected to `/` with title `Calibre-Web NextGen | Books (42)`.
- Classic authenticated accessibility surface exposed `Switch to New UI` linking to `/app/`.
- Post-sign-in cookie metadata was inspected without values; authentication remained represented by `session` (`HttpOnly`, `SameSite=Lax`, path `/`).

## New UI Account sign-out — OBSERVED

- `/app/` loaded with title `Your Library · Calibre-Web NextGen`; accessibility snapshot showed `Account: admin` and the visible expanded menu with `My account`, `Admin`, `Back to the classic view`, and `Sign out`.
- The visible New UI `Sign out` button was activated.
- Immediate and reloaded final URL: `http://127.0.0.1:8101/app/`.
- Exact final title: `Sign in · Calibre-Web NextGen`.
- Final accessibility surface: main sign-in form with heading `Sign in`, Username textbox, Password textbox with `Show password` button, checked `Remember me` checkbox, and `Sign in` button. No Account menu or authenticated library controls were present.
- Preference cookie: absent on the clean initial login page; present after logout as `cwng_prefer_spa`, host `127.0.0.1`, path `/`, `Secure=false`, `HttpOnly=false`, `SameSite=Lax`, with an expiry; value intentionally omitted.
- Authentication clearance: `remember_token` was present after sign-in (`HttpOnly`, `SameSite=Strict`) and absent after logout. A `session` cookie remained after logout (`HttpOnly`, `SameSite=Lax`), but the post-logout `/app/` surface stayed anonymous after reload and `/api/v1/auth/me` returned observed `401 (UNAUTHORIZED)`; this confirms the remaining session cookie was not an authenticated session.
- Console: observed one error class, `Failed to load resource: the server responded with a status of 401 (UNAUTHORIZED)` from `/api/v1/auth/me`; the console tool reported no warnings (its severity-inclusive output repeats the error when queried at warning level). No other warning was observed.

## Evidence boundary

- OBSERVED: browser actions, URLs, titles, accessibility snapshots, cookie names/attributes without values, body class/computed styles, and console response/error output described above.
- ASSUMED: none for the reporter flow conclusion. The meaning of the surviving anonymous `session` cookie is supported by the observed anonymous UI and observed `401` auth probe, not by decoding its value.

Status: COMPLETE
