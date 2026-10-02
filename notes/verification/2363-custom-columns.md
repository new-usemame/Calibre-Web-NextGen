# Custom-column adoption verification

This adoption credits @Rol3333’s original implementation in #2363. It makes New UI browsing agree with Classic and OPDS while keeping Calibre’s configured hierarchy separate from atomic dotted values. Explicit reader choices win; otherwise hierarchy is visible and flat columns hidden. The shared profile editor stores changed choices, and Categories controls access to all three browse surfaces.

## Product decisions

Calibre’s `categories_using_hierarchy` preference chooses browse mode, including empty or leaf-only trees. The read uses the active library’s fixed `main` or `calibre` schema. Only older libraries without that preference table use legacy content detection. Missing preference entries mean flat values. Per-request engine-scoped reads expose Calibre changes immediately; no unmeasured performance improvement is claimed.

The existing per-column-ID visibility contract stays shared across interfaces. Changing libraries can reuse IDs, so the guide tells administrators to review personal choices. A one-time compatibility upgrade preserves previously visible legacy columns without freezing hidden defaults. It records the highest existing account ID before serving account creation, so retrying an unavailable library never stamps newer accounts. The completion flag and account boundary are additive app.db fields. Calibre metadata is never written.

New UI uses native disclosure buttons beside navigation links and semantic lists, with 44-pixel disclosure targets. Failed definitions, modes or values stay unavailable responses; retry remains visible. Pagination resets on category changes and hides old-category results while loading. After children places books inside an existing selected node; a stale bookmark still exposes its request error. Shared book cards preserve permissions and display preferences, including metadata browsing without the viewer role. Classic sort links keep slash-bearing values on the existing custom-column route.

Advanced Search retains partial matching. Editing hierarchy trees and New UI book-detail breadcrumbs are deferred because they are separate editing/detail flows. Classic detail links respect Category and per-column visibility; hidden values remain plain metadata text.

## Evidence and limits

Independent original-context and fresh-context Claude reviews ran, with an explicit security review of the new GET routes and visibility/migration boundaries. The fresh security pass found no leaks: hidden browse paths return404, shelf/library filters remain applied, fixed schema names are allowlisted and values are SQL parameters. The final repaired source still requires reviewer follow-up and exact-head CI.

Behavioral regression evidence covers Calibre preference modes and attached schemas, Guest compatibility, profile choices, genuine HTTP failures, stale-node pagination and preference-sensitive card actions. Seven further checks were all seen red when strict failure handling or the account boundary was removed, then passed repaired: three mode-failure HTTP routes, two mapped SQLite value-query failures, delayed upgrade account exclusion and a physical old-schema migration/restart boundary. The final focused packet passed165 with one existing locale-tool skip; its subsequent strict-boundary/detail packet passed33. Frontend units/typecheck/production build passed197.

A complete earlier image `0cf9b010185c6e3741f71384a119e4e75945cee6a8eab25a8ee8c27d8117626c` passed four real-library cases across Chromium desktop/phone and native WebKit desktop/iPhone in52.3seconds. The private clone used actual Calibre9.11 columns/preferences, hierarchical slash values, atomic Dewey values and `AC/DC`. The flow exercised profile enable/hide, exact API results, Classic sorting/detail links, OPDS acquisitions, real account theme saves and light/dark axe checks with zero page errors. That proof predates the final strict-read/account-boundary repairs; it is not relabeled as current-image proof.

Earlier full local suites retained the two Mac SQLite replacement/I/O failures also observed on the baseline. The pre-strict full run passed10,391 with103 skipped and four failures: the two baseline failures plus two newly introduced French/Dutch fallback gaps, subsequently repaired with67 focused passes and one existing skip. Final frozen full-suite and rebuilt-image results remain pending here. First recreated-image browser attempts hit startup connection resets, then private sort checks used the wrong accessibility role; corrected native menu selection passed. All failed runs remain recorded without suppressing assertions.

No physical-device, VoiceOver, proxy-subpath, published-release, deployment or clean-server-log claim is made. The isolated clone retains inherited malformed synthetic EPUB startup errors. Release is a separate operation.
