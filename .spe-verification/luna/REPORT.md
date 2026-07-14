# Issue #843 first-run capture

Observed against the isolated Docker web app at `http://127.0.0.1:8105` using the documented default admin credentials. Screenshots are page-only JPEGs; browser chrome and local machine details are excluded.

- `docs/install/images/verified/01-login.jpeg` — Initial unauthenticated login screen at `/login?next=%2F`. Visible state: Calibre-Web NextGen heading, Username and Password fields, Show Password, Remember Me, and Login.
- `docs/install/images/verified/02-library.jpeg` — Authenticated classic landing/library at `/` after successful login. Visible state: `Books (0)`, search, browse/sidebar navigation, empty library, logged-in admin notice, and the genuine first-run/switch prompt: “A faster, redesigned Calibre-Web NextGen interface is ready. Your classic view stays the default until you switch. Try the new UI”.
- `docs/install/images/verified/03-new-ui.jpeg` — Authenticated new UI landing at `/app/` after selecting “Try the new UI”. Visible state: Library navigation, Your Library, Upload books, filters/sort controls, and an empty Discover section.

No forced password-change screen or other setup screen was presented. These captures verify Docker-container web-app behavior only; no NAS hardware behavior was tested.
