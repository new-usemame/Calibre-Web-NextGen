# Shared catalog search verification

This slice adds browser orchestration over the existing authenticated acquisition catalog API. It changes no catalog adapter, download client, database schema, permission migration or import worker. Anna's Archive and per-user private connections remain outside scope.

## Behavioral coverage

`frontend/unit/acquisitionSearch.test.ts` covers advertised opaque search dispatch, mixed searchable/browse-only/failed sources, bounded batches, invalid input before I/O, fast partial results, cancellation and rejection of upstream exception text. The author observed these behaviors fail against an unimplemented orchestrator before implementation.

`frontend/e2e/acquisition-search.spec.ts` exercises the served SPA on desktop and a 375px phone: separate editions, correct connection on a request, opaque source pagination, failed versus empty/browse-only states, isolated retry, explicit remaining batches, old query cancellation, withdrawal/re-enable invalidation, keyboard submission and serious/critical axe checks. The shared option assertion also failed against the previous slice's actual SPA bundle.

An independent refuter reproduced stale results returning after a source was withdrawn and re-enabled at the same revision. The fix permanently invalidates that source for the current query. The regression test failed against the previous bundle, then passed against the fixed one. An independent clean round checked same-revision withdrawal/re-enable, late responses, revision changes, fresh queries and permission 404 after the existing API retries settle.

## Runtime proof

An owned disposable container searched three local legal fixtures through real authenticated API calls: OPDS 1/OpenSearch, OPDS 2 keyword search and Newznab book search. The browser requested the German OPDS 2 edition, with the correct source connection ID. Its original EPUB passed through normal ingest into an imported request with a durable receipt; its German chapter rendered in the normal SPA reader. The source receipt hash matched the German artifact and differed from the English OPDS 1 edition. The final reader check reused the existing request and did not submit another download.

The Newznab fixture demonstrates discovery only. Its fake client binding was neither probed nor used for download; no new real download-client compatibility claim is made. Earlier client verification remains in [the client record](virtual-library-clients.md).

Independent desktop/phone and light/dark checks covered populated, failed, browse-only and remaining-source states, long labels/titles, keyboard submission, visible control size, horizontal overflow, console errors and critical/serious axe violations. All four cells passed. These are Chromium checks, not Safari, physical touch or VoiceOver evidence.

All eleven new strings are translated into German, French, Hungarian and Dutch. All 28 gettext catalogs compile. Authenticated runtime checks on a freshly rebuilt image verified all eleven dictionary values and actual mixed-catalog search rendering in each of these four locales, then restored the disposable account’s locale. Immutable CI image checks are recorded in the PR.

## Gates and limits

The full local suite ran once: 10,271 passed, 103 skipped and three failed. One introduced TypeScript test used a newer array method than the harness target permits; its syntax was corrected and the owning typecheck/unit lane passed. The other two failures are existing macOS SQLite metadata-file replacement cases, outside the changed files. Linux current-head CI must pass those cases; no test was suppressed or gate relaxed.

The conditional security review inspected the nonempty product diff, including the shared API AbortSignal plumbing, source-bound requests and existing authorization boundary. It found no high-confidence vulnerability.

A fresh independent merge review reproduced phone overflow from an accepted 84-character unbroken query. The author reproduced that failure and the same failure with a long configured catalog label: at 375px the page expanded to 553px and 730px respectively. Scoped status and heading wrapping fixes both. The owning browser regressions assert that the populated page stays within 375px and 320px viewports; both were seen red before the CSS fix. The search suite passes all fourteen desktop/phone cases, including keyboard and serious/critical axe checks. Frontend units and typecheck/build also pass.

Current-head CI and immutable multi-architecture image checks are required before merge. The operator authorized a guarded merge after independent review and disposition of findings. No release or production deployment is part of this slice.
