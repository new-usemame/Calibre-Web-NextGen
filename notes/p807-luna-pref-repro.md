# CWNG #807/#739/#733 — final preference reproduction report

## Scope

- [OBSERVED] Final capture used the existing leased browser at `http://127.0.0.1:8101/login`.
- [OBSERVED] Viewport was 1280x800.
- [OBSERVED] No navigation, click, cookie clearing, browser close, code change, GitHub action, or Docker action was performed during this final capture.

## Preceding run facts

- [OBSERVED] The initial classic login after the temporary default-theme override had body class `login` with no body `blur` token.
- [OBSERVED] The earlier stale note claiming body blur came from a superseded `caliBlur` run and is removed from this report.
- [OBSERVED] `admin` / `admin123` login succeeded.
- [OBSERVED] Authenticated New UI at `/app` showed `Your Library`, 42 books, and account `admin`.
- [OBSERVED] Before logout, cookies included `remember_token`, `cwng_prefer_spa=1` (Path `/`, one-year expiry, HttpOnly false, Secure false, SameSite Lax), and an authenticated session.
- [OBSERVED] `Account: admin` was opened and the visible `Sign out` control was clicked.
- [OBSERVED] Logout landed at `http://127.0.0.1:8101/login` with the classic `Calibre-Web NextGen | Login` title.

## Final capture

- [OBSERVED] Accessibility snapshot URL: `http://127.0.0.1:8101/login`.
- [OBSERVED] Page title: `Calibre-Web NextGen | Login`.
- [OBSERVED] Visible surface contains the `Login` heading, Username and Password fields, Remember Me checked, and Login button.
- [OBSERVED] Current cookies are `cwng_prefer_spa` and `session`; raw cookie values are intentionally omitted.
- [OBSERVED] `cwng_prefer_spa` remains present with Path `/`, HttpOnly false, Secure false, SameSite Lax, and its observed one-year expiry configuration.
- [OBSERVED] `remember_token` is absent.
- [OBSERVED] The remaining `session` cookie is not authenticated, as shown by the classic login surface and absence of `remember_token`.
- [ASSUMED] The remaining session is an anonymous session; this is inferred from the unauthenticated page and cookie set, without decoding its value.
- [OBSERVED] The preference survived logout: yes.
- [OBSERVED] Symptom reproduced: yes — logout preserved `cwng_prefer_spa=1` but landed on the classic login surface rather than the New UI login surface.

## Console

- [OBSERVED] Current console collection reported zero errors and zero warnings.
- [OBSERVED] One verbose browser message reported missing `autocomplete` attributes on login inputs, suggesting `current-password`.

## Evidence boundary

- [OBSERVED] The final page state comes from one accessibility snapshot.
- [OBSERVED] The final cookie state comes from the requested Playwright context-cookie read, with values excluded here.
- [OBSERVED] Console state comes from the console collector.
