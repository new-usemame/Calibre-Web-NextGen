# Completed-download book choice verification

Owned multi-book downloads wait for explicit requester choice. Individual choices follow the ordinary approval, publication, import-receipt and library-visibility rules. This is separate from discovering a release or completing its remote download.

## Behavioral checks

- Bounded SAB/NZBGet and reported-list torrent enumeration preserves path, ancestor, symlink and regular-file guards. New enumeration tests failed before implementation; 99 enumeration/client/Usenet checks passed afterward.
- Encrypted owner-bound manifest, first/later/catalog replay, stale generations, cancellation and connection fences, concurrent selection and ordinary worker flow have owning regressions. The populated v1 database upgrade failed on the old schema and passed with the additive migration, including a cold repository restart and preserved grants, jobs and receipt.
- New authenticated choice routes were seen red before implementation. Current API checks cover cross-account rejection, current grants/formats/approval, disabled feature and paused worker. Combined owning/i18n checks passed (163 tests, one local-only CI availability skip); the final string-only i18n gate passed 134 tests, with the same skip. All 28 locales compile.
- Independent backend refutation passed all 429 adjacent acquisition tests and extra same-size mutation and symlink-replacement probes. Neither bad file published, produced a receipt, fell back to another book or resubmitted the download.

## Actual native runtime

Installed Calibre 9.0 and the normal unmodified ingest processor exercised a legal loopback SAB HTTP fixture. Actual form and multipart requests passed through the production transport subprocess. Two chosen books imported separately for one account; another account received its own manifest, job, receipt and membership for the exact first book. Calibre retained that existing identical book. Exactly one client submission occurred, and client source paths, count and bytes stayed unchanged. Waiting jobs were not polled by worker claims. This native proof does not stand in for candidate-image CI.

The real SPA, API, session login and CSRF checks were exercised on an isolated native app with its scheduler paused. Two real UI selections returned distinct ordinary sibling jobs. Completed-import links and failure states use account-local presentation fixtures in the browser spec; actual import/receipt correctness is covered by the runtime probe.

## Browser and translation checks

The new keyboard-choice browser regression failed on the pre-change SPA built from the merged slice6 source, because no candidate action existed. It passed with the current SPA. Six browser scenarios cover first/later choices, receipt-only Open links, stale choices without substitution, failed-list retry, paused reads, cancelled selected parents, long filenames and both palettes at 1280, 375 and 320 pixels. All scoped layouts had zero serious/critical axe violations. Independent UI refutation found focus lost after a successful choice; a seen-red focus regression now verifies transfer to the persistent disclosure. The successful parent response updates its known queued state even if the background activity refresh fails. Frontend typecheck/build and 212 unit tests passed.

New text is anchored for Babel extraction and translated in German, French, Hungarian and Dutch. Real session profile saves and authenticated user payloads drove the panel in all four household locales at 375 pixels without overflow. Final independent UI recheck, current-head CI and immutable image checks are recorded with the PR's final verification.

## Security and limits

The actual high-effort read-only security review found no high-confidence vulnerability in the new routes, encrypted identity, owner/admission fences or path handling. No real provider accounts or household services were used, and no dependency was added.

Generations are immutable snapshots. Changed files require restoring the original bytes or a separately identified fresh release; already imported choices are not automatically replaced. Before downgrading, pause acquisition and settle or cancel all bundle work. An older worker must not resume outstanding selected-artifact jobs. Keep the additive schema intact. No per-user private connections or Anna sources are included.
