# Private book reviews (#917): verification

## Product decision

The remaining reporter ask is a personal review/note under a book, rather than another shared metadata comment or rating. Each real account gets one plain Unicode text per visible book in app.db. New UI and Classic use the same API and saved text. Access follows book detail, including its deliberate archived/hidden behavior; writing a note does not require metadata-edit permission. It changes no shared Description, stars, read status or progress. Normal writes accept 10,000 Unicode code points; duplicate merges preserve both distinct notes, including an oversized merged note, rather than discard authored text.

## Behavioral seams and fixes

- A real SQLite model is unique by account/book. GET/PUT/DELETE require a real signed-in account and current book access. Reads and all errors are private/no-store; no requested owner ID bypasses the current-user scope. Upserts are atomic. A concurrent-delete seam first produced a 500 after a successful commit; the response now returns that committed mutation directly instead of rereading a possibly deleted row.
- Account isolation, Guest rejection, duplicate-text preservation and user/book purge tests were each seen red after removing their protection, then restored and passed. The 27 backend/SSOT tests use a real in-memory SQLite database.
- Screenshot inspection found Classic .btn rules overriding native [hidden], making Retry visible during a healthy state. The actual browser assertion was red before a widget-scoped hidden rule; the rule lives in the included widget so full pages and XHR fragments both receive it.
- Classic fragment probing then found an existing 500: delete_book was imported only by layout.html, while detail.html can also extend fragment.html. An explicit detail-local macro import fixes that seam. The real XHR response now renders, and the widget initializes after DOM readiness with duplicate-initialization protection.

## Real browser and runtime proof

Final checked-in book-review.spec.ts: **6 passed, 1 skipped**, no retry. The six passing cases include global setup, both interfaces at 1280×800 desktop and 375×667 touch-emulated Chromium, and a desktop-only account/API boundary case. The same boundary test is intentionally skipped on mobile; both UI flows run there.

The flows use owned real accounts without admin or metadata-edit rights, actual book detail and actual persisted reviews. Only the explicit PUT503 failure is routed. They exercise failed-save draft retention and retry, literal HTML attack text without an img element, whitespace/Unicode, reload, reading the same note in the other interface, cancelled edits, confirmation/deletion, keyboard focus, and unchanged shared metadata/read fields. Classic fetches the real XHR fragment and loads the real script after readiness. Twelve named JPEGs cover saved text, failed-save form and removal confirmation in both interfaces/widths. Root visually inspected the final phone Classic saved state and desktop light retry form.

Scoped Axe checks cover the New UI populated editor, deletion dialog and empty widget at desktop light and phone dark. Keyboard checks cover Cancel initial focus, Tab/Shift+Tab wrapping, Escape/trigger restoration and heading focus after deletion. No critical/serious rule was suppressed. No physical device, VoiceOver or Safari/WebKit claim is made.

Six simultaneous real PUT requests return200 and retain one current note. Missing CSRF is400; cross-site mutation with a valid token is403. A separately authenticated administrator sees its own empty review, including when another user_id is supplied. Independent owned-user HTTP checks confirm denied-tag policy blocks detail plus review GET/PUT/DELETE with404; archived and hidden book state retains detail/review200. Unauthenticated review GET returns JSON401/private-no-store. Global/public-shelf/column/language behavior follows the inspected shared visibility funnel; it was not independently exercised in this live matrix.

A deliberate cold restart preserves the exact review text and update timestamp. The real API accepts10,000 owl characters and rejects10,001; deleting the owned account leaves zero review rows, checked inside the running Linux container. An earlier proof read app.db from the Mac across the Docker volume after restart and got disk I/O; its successful API persistence/cleanup results and failed host-read trace are preserved. The final database-count probe uses the container filesystem/SQLite runtime.

## Artifact identity and checks

Final whole image: `sha256:05e3fdf2f82874f324de7d91a24ccf4c86655cf85619c7cfced6a3d6d13ca42a`. Actual HTTP bundle `/static/app/assets/index-DirqgK9H.js`, 973761 bytes, SHA256 `db65c4f714d91aac9934c8604bef62c6995caae5a898a1c43ee9d71cc0c55000`; delivered bytes match the container file. Seven API/model/SSOT/template/static-script source files match the worktree hashes. Evidence is preserved privately as root-final-provenance.json, browser-final-xhr.log, root-restart-proof.json, independent visibility proof and final-captures.

One full local smoke/unit run: **10,273 passed,103 skipped,7 failures**. Two metadata-replacement SQLite I/O cases are the observed clean-main baseline. The other failures were a derived classifier documentation count, malformed new fragment, and three template-harness failures because its DictLoader could not load the new include. The documentation/fragment were corrected and the fixture now loads production templates behind stand-in layouts. **84 affected checks** pass; **107 translation/anchor/catalog checks and one existing skip** pass; a further10 affected checks pass after the explicit macro import. No second full run is claimed. Final product/E2E TypeScript, Ruff, JavaScript syntax and diff checks pass.

Private build evidence includes one Docker Hub frontend TLS handshake timeout before any container mutation and a setup attempt started during an owned rebuild; neither counts as a product/browser pass. Final proof ran only after health. Existing invalid EPUB seed files also produce unrelated scheduled KEPUB backfill errors; no review endpoint traceback remains in the final flow.

Independent source/privacy/security review includes actual owned-user visibility evidence and final source/browser/capture review. CI is the remaining integration gate for this review candidate.

## CI follow-up — run 36936176504

The broad CI run had two deterministic E2E failures in the existing #1169 catalog-edit
regression, desktop and mobile. Playwright traces showed the only failing resource was
`GET /api/v1/books/9001/review` returning404: #1169 uses an in-memory synthetic book
without a database row, while the new signed-in detail widget correctly asks the review
API for that book's private state. The spec now gives that synthetic fixture an explicit
empty-review response (`{ review: null }`). Its `assertNoPageErrors` check remains intact.
The unrelated SC 2.5.8 target-size cases were flaky in that CI run (their initial
measurement saw a temporarily hidden control; retries passed); this fixture correction
does not alter those cases.

Against a fresh whole-worktree private image (`calibre-web-nextgen:promised-917`, image
`sha256:fe6b325687cfd35204dad5f5364fc8986929820d6f6d6ed3ba3cc5063c4457b1`, one boot),
the served bundle was `index-DirqgK9H.js` (`db65c4f714d91aac9934c8604bef62c6995caae5a898a1c43ee9d71cc0c55000`). The #1169
“book is still listed” case passed for desktop and mobile with no retries: **3 passed
including setup**. The broad suite has not been rerun locally; the pushed correction
restarts CI for that gate.

## Serial current-main integration — 2026-10-02

Rebased onto main `e2b4233be` after uploaded-font PR2418. API registration retains reader_fonts and adds book_reviews; all preceding changelog rows and SPA strings remain. Current account Kobo/default UI-font settings and the complete book-detail surface remain intact. Product feature files remain unchanged from the independently reviewed original tree; current focused checks, independent bounded integration and exact-head CI are required before merge. Derived Python import closure is219 of277 modules. Original actual image/browser/security/concurrency/restart and full-failure evidence above remains historical; this rebase does not claim a repeated full local suite or current whole-image run.

Current focused schema/private-review/SSOT cleanup/detail/locale/anchor/classifier/immutable-image resolver packet: **177 passed, one existing skip**. Frontend units **201 passed**, both TypeScript checks and production build passed. Original feature service/widgets/storage/tests are byte-identical; the shared catalog fixture retains main’s new cover disclosure/native tap interaction from PR2416 alongside this feature’s explicit empty-review response for its synthetic book. Original actual browser/concurrency/restart evidence remains applicable to unchanged feature behavior and is not presented as a new current-image run. Fresh exact-head broad CI and independent additive integration are pending.
