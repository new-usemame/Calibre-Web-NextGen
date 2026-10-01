# Cover-card actions — #1412

The New UI now offers one Actions disclosure within the cover, rather than a read/edit row under its metadata. Desktop reveals it on hover or focus; touch keeps the 32px disclosure visible and opens a sheet with 44px action targets. Permission-aware Read now and Edit remain links. Personal favorite/read actions use the existing endpoints; shelf removal shares the panel.

## Product decisions

- One disclosure makes the requested shortcuts available without filling small covers with several controls. The existing saved hide-actions preference removes the disclosure and its tab stops. Selection mode retains one selection toggle. Unowned Global Library cards keep their Add action.
- Reading is shown from the existing reader/sync state. The panel offers Mark as read/unread rather than manufacturing a manual intermediate progress state. Mark as unread retains the existing endpoint's reading-position reset behavior. The separate lookup-reading work is not required by this change.
- Favorite/read changes rebuild the catalog from page one, including Favorites, Read/Unread and saved default filters. This deliberately resets pagination/scroll: refreshing one accumulated page leaves stale members and cannot repair shifted page boundaries. If the active card disappears, focus moves to the list heading; ordinary dismissal restores its disclosure.
- Covers at most 126px wide show status icons with complete accessible names and one shelf count with the full names in its label. Wider covers retain visible labels. This keeps the controls and four status badges within a 68×102px dense cover; long labelled pills did not fit.
- Favorite state is current-user metadata fetched in one bounded query per page. An optional app-DB failure returns unknown (`null`), preserves the readable catalog and omits the favorite action instead of guessing its state. Write failures remain visible and retryable.

## Verification

Independent review reproduced and resolved stale Favorites membership, incorrect opener capture and the dense-cover badge collision. A separate real-reader probe observed Reading after a normal open and Read after the panel action. The final independent verdict is clear.

The complete set of eight touched browser specs finished **50 passed, 3 skipped, no failures**. It covers actual favorite/read persistence and reload, filtered membership/counts, saved preferences, a rejected mutation and retry, selection, shelf removal, real Edit navigation/new-tab behavior, permission rendering, keyboard focus/trapping/Escape and Axe. Comfortable, Compact and Dense layouts were measured at 1280×800 and 320×740, light and dark. Fourteen fresh JPEGs include the worst badge/shelf stack. The mobile project uses Chromium `isMobile`/`hasTouch` emulation; no physical-device or screen-reader claim is made.

Final private image: `sha256:87e6df6e488ef4c5d0c91fea9944a1a4b0d22abda70800e29e98de67b3d315ec`. The actual served `/static/app/assets/index-D2IvAkHa.js` is 973,392 bytes, SHA-256 `0a870af74b36e38e2fad2f7599b8605c52fddcdbaca36fed70f19b84510a8709`. Runtime `cps/api/books.py` matches the worktree (SHA-256 `bbbaa8377c9758a7c9b18bbc7374965c07c28c639c983a1fcb2b0f4c26ab7557`). The final browser run and independent geometry probe used that image, with one healthy app boot and no container restart.

One full local smoke/unit run finished **10,246 passed, 103 skipped, 10 failed**. Two metadata-replacement SQLite I/O failures reproduce on clean main. Five obsolete row/provider/database fixture expectations were corrected, a temporary private probe caught by the environment-documentation scanner was removed, and the old pencil source pin was replaced by real Edit/new-tab browser proof. The remaining failure exposed the optional favorite-query 500 and is fixed by the unknown-state fallback. Final affected backend checks: **34 passed**; affected React rendering checks: **11 passed**. The broader frontend unit run passed **192**. Type checks, locale/anchor checks and changelog validation pass.

Earlier browser attempts were disrupted by one private app-service restart while the local full suite ran. There was no Docker OOM event; its cause remains unproven. The final isolated browser run above had no restart or failures. Classic cards are unchanged; this request and its screenshots concern the New UI.
