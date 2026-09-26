# Pre-release audit — 2026-09-08

This document preserves the original audit baseline before corrective implementation. Subsequent fixes and combined verification are tracked in draft PR #2208; the original red results below are retained as regression evidence, not the current branch status.
**Verdict: do not cut this candidate yet.** The audit found seven new reproducible correctness/recovery defects, plus an unresolved intermittent accessibility failure. A separately tracked Kobo upgrade regression is also present in main. Existing focused tests and many live user flows pass, but those results do not support a regression-free release claim.

## Audited subject and scope

- Baseline: last published release `v4.1.43`.
- Candidate: main `1ec1bfe6773ddd9a4d2832343445a588e1b37617`.
- Delta: 254 reachable commits, 960 changed paths, including translations, tests, docs and tooling. The pass inventoried the complete delta and used risk-based source review and behavioral verification; it does not claim every changed line received exhaustive review.
- Open PR snapshot: #2207, #2170, #2148, #2115, #2095, #2077, #2062. Pending branches were not all merged into a single tested candidate.
- Three independent reviewers covered upgrade/recovery, account isolation, and auth/proxy/bootstrap/ingest/deletion/reader/pairing. Second independent reviews reproduced bulk-enable persistence and Magic Shelf cache failures and checked the catalog diagnosis.
- Product code was not modified. This branch records audit findings only. No merge, tag, release, production deployment or public message was performed.

## Release-relevant findings

| Priority | Finding | Evidence and impact |
|---|---|---|
| P1 | Existing Kobo upgrade ledger reset — already tracked in #2207 | Real handler, 218 acknowledged held books, settled token: upgrade produced New entitlement batches `[100,100,18,0]`, expected none. Main `cps/kobo.py:502-511` deletes old device ledgers. The proposed fix retains the single-Kobo ledger but deliberately retains different behavior for multi/retired-Kobo accounts. No physical-device outcome was newly measured in this audit. |
| P1 | F-5a00f2: LDAP first-login provisioning fails | Valid directory bind and details lead to `cps.api.admin` AttributeError and HTTP401; actual helper is in `cps.admin`. Real Flask route with mocked external-directory and user-query seams. New default SPA makes this a release compatibility concern. |
| P2 | F-875e25: correct LDAP logins consume failure limits | Four valid existing-user logins through actual memory-backed limiter: `[200,200,200,429]`. LDAP success omits rate-limit cleanup present on local login. |
| P2; release blocker for promised Undo safety | F-1bf0c0: bulk enable can lose its original snapshot | Stop after real durable account commit, close/reopen SQLite session: user changed, intro still not_enabled, snapshot absent. Retry plus Undo leaves changed flags rather than original values. Independently reproduced. |
| P2 | F-e95ca6: partial bulk enable cannot retry failed accounts | Temporary per-account seed failure still marks global operation enabled. Later enable returns no work. Ordinary rollback can also leave a previously committed role bit changed. Undo then enable is a workaround. |
| P2 | F-36b518: public shelf links terminate at inaccessible resources | Managed personal viewer sees shared book listing, but real download helper raises404 and detail/cover policy disagrees. Same account in monolibrary resolves book. Route policy traced; helper/SQLite behavior reproduced. |
| P2 | F-7d3396: warm Magic Shelves ignore membership changes | Warm `[1,2]`, remove1, new request still `[1,2]`; uncached query `[2]`. Persistent cache lasts30minutes and membership mutation does not invalidate it. Reproduced independently. No claim that this defeats Kobo's separate removal intersection. |
| P2 | F-ea392f: SPA restores obsolete catalog cards | Two live UI flows fail: catalog→detail→remove→Library, and whole catalog→Account→saved My Library→catalog. Server excludes the book; browser retains card for the same session. Both reload controls remove it. Query invalidation leaves scroll snapshots and append-only page accumulator stale. |
| Investigate before clean accessibility sign-off | F-c949c9: mobile target-size failure | Existing My Library mobile case reported undersized format download target after membership/shelf action. Isolated repeat passed. Original failure retained; no unsupported deterministic root-cause claim. |

Membership is a selection boundary, not a separate physical library or general content-authorization boundary. Existing language/tag/restricted-column controls still matter. **No new cross-account private annotation/device/cover exposure was demonstrated in the reviewed paths.** This is bounded negative evidence, not a security certification.

## What passed

### Existing focused backend suites

- Upgrade/recovery reviewer:147 passed.
- Account-isolation reviewer:180 passed.
- Release-surface reviewer:243 passed.
- These are570 passing test executions across focused runs, with overlap; they are not570 unique tests or a complete suite run. Additional adversarial probes were red as described above.

Covered seams include synthetic populated-schema migration and rollback/re-migration, seed-once behavior, concurrent managed-library removal, retained annotations/progress, ownership and restricted catalogs, device-delivery authorization, local authentication, proxies/origins, app factory, ingest overwrite/retry, pairing and reader-resume decisions. Some tests replace collaborators or bootstrap portions of the application.

**Migration provenance:** the populated pre-feature fixture was synthesized from then-current models and stripped of My Library schema with the rollback helper. It is not an app.db produced by the real v4.1.43 image. A genuine populated tagged-image-to-candidate upgrade remains a distinct release gate.

### Built image and real HTTP/browser flows

An isolated image was built directly from the audited commit, including the actual production SPA build, and booted healthy with one startup. The candidate was not mounted over a different running image.

- Image ID: `sha256:bcb1470030441ea6767282074be0dbfecd4245f5e14695c8313f516a3f58bbb4`.
- Served bundle: `index-DVqAe7No.js`.
- Bundle SHA256: `d83149a59a39b44da719b55c8b27da6918760daeb0309fdb9fa0a45a0e314b82`.
- Frontend typecheck plus unit suite:85 passed, zero skips.
- My Library browser matrix:21 passed,1 failed,1 skipped (mobile duplicate of Classic parity). Pass counts include setup. Kobo sync was enabled, so the removal/archived-entitlement test actually ran on desktop and mobile.
- Admin intro browser happy path:2 passed including setup; Try→Undo→Close works without interruption.
- Accessibility/keyboard routes:25 passed,10 skipped. Skips include deliberately excluded phone routes and reader-fixture absence; this is not full mobile or screen-reader coverage.
- Two new same-document catalog navigation cases were red. A corrected mode-switch locator was required before that second case reached product behavior; its initial timeout was a probe defect and is excluded from findings. Corrected run and explicit reload controls confirmed both defects: before reload1 obsolete card, after reload0, server excludes ID throughout.
- Separate mobile accessibility repeat:2 passed including setup. This diagnoses intermittency; it does not erase the initial failure.

Confirmed live user flows: independent personal selections; Global Library add/cover/detail; managed no-browse restrictions and last-book refusal; empty-library recovery; retained annotation page, exports and device views; Classic library parity; actual Kobo archived entitlement after UI removal; successful bulk-intro enable/undo/dismiss.

The existing Calibre9.11 runtime probe ran inside this same built image. All host-contract assertions passed: overwrite success produced one committed book and marker with incoming file hash; injected precommit failure restored original file bytes, kept one book and left zero marker. The injected traceback is expected evidence, not an unexplained runtime error. The private seed also contains malformed EPUBs; Kobo test logs recorded three known invalid-ZIP metadata errors. No clean-all-logs claim is made.

## Pending PR disposition

| PR | Audit disposition |
|---|---|
| #2207 | Required Kobo upgrade fix; head moved during review. Verify final head and multi-device behavior explicitly. Tested main does not contain it. |
| #2170 | Hierarchical custom-column browsing; partial Classic/OPDS filter-seam review. Needs actual custom-column multi-user fixture and full flow before merge certification. |
| #2148 | Findings-only reopens a target-firmware failure. Preserve the hardware limitation in release claims. |
| #2115 | Cross-view custom-column sorting, conflicting at snapshot. Resolve against current membership/global-library code and verify final result. |
| #2095 | CI server-state config guard; diff reviewed. Does not certify product behavior itself. |
| #2077 | Draft legacy-plugin migration notice. Device update path not executed here; text-pin tests cannot prove that path. |
| #2062 | Old release candidate, conflicting, with runtime Kobo code as well as release metadata. Its green historical checks do not certify current main or a newly assembled release. Its body incorrectly says no release hold exists and describes an older, smaller delta. Rebuild its description/artifacts around the actual candidate. |

Git Manager bearer authentication returned403, so fresh operator annotations could not be read. GitHub and local standing instructions provided the PR/release evidence; this gap is not treated as an empty feedback field.

## Required before release sign-off

1. Correct the reproducible failures and retain behavioral regressions that have been seen red. The catalog checks must navigate within the SPA, not reload between every action; recovery checks must reopen durable state after interruption.
2. Verify the final Kobo fix for existing single-device, multiple-device and retired-device accounts. A known forced reannouncement needs explicit disposition; do not assume single-Kobo preservation covers every household.
3. Assemble one immutable candidate containing the intended merged changes and current changelog, What's New and plugin version artifacts. Retest its exact SHA rather than borrowing checks from stale release branches.
4. Run a populated upgrade from the actual last-release image, repeated cold boot, and failure/recovery cases with personal data and account restrictions; extend wire/device evidence where a physical client supplies the only oracle.
5. Recheck LDAP with real directory integration, public shelf link-to-download continuation, both cache layers, mobile accessibility, subpath proxy, and household Safari/VoiceOver. The present audit did not cover every matrix cell or run a full mutation sweep.
6. Keep the existing operator release hold intact. This audit request does not clear the prior request for household dev acceptance.

There is no defensible95% or regression-free claim here: multiple consequential checks are red and final-candidate gates are absent. The useful result is a concrete repair list, passing evidence with limits, and reproducible failures before users receive a release.
