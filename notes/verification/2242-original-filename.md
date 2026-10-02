# Original filename display preference (#2242)

The setting is per account and defaults on. Existing imports and API data are preserved: this is a display preference, not an access restriction. The normal book-detail pages honor it in classic and the New UI. Metadata-editing screens keep the diagnostic source name; the New UI editor now displays it as the classic editor already did.

Storage uses the existing named preference allowlist in `User.view_settings`, with null meaning unset/default-visible. The New UI checkbox saves immediately through the existing preferences endpoint. Classic uses its normal profile Save action and a hidden presence sentinel so an older/partial form does not reset the choice. Anonymous users cannot write a shared Guest preference.

## Evidence

The new preference response test failed before registration. A further rendered-template test failed when the setting argument was omitted; classic now hides only for explicit false, preserving default visibility for older render callers too.

Five new backend behavioral tests exercise default/isolation, Guest denial, partial save preservation and actual classic template rendering. Existing named-preference response fixtures were updated to include the new nullable field. Final named-preference/default/template run: 30 passed. Combined preference and locale-completeness run: 67 passed. Translation compilation/SPA anchors: 81 passed, 1 skipped. Frontend units/TypeScript: 192 passed.

Portable browser tests passed at desktop 1280px and phone 375px against an image built from this branch. They save off in the New UI, verify the stored value, retain the source in metadata editing, save back on through classic profile, verify it in the New UI, and confirm another account is unchanged. The fixture injects only a filename into an otherwise real book-detail response; auth, users, roles, preferences, profile forms and persistence are real. The account page passed its critical/serious accessibility scan.

Supplementary private-seed browser checks used an actual stored filename on book 180, without response interception: classic detail showed it with the preference on and omitted it with the preference off, while the API retained it. Both desktop and phone checks passed. The first supplementary attempt used a stale filename record for removed book 172; this fixture was corrected before the passing run. The private-only spec is archived with the evidence, not shipped as a CI test tied to this particular seed.

Eighteen JPEG captures cover New UI visible/hidden detail and account settings in light/dark, plus classic profile and actual classic detail at both widths in the served caliBlur theme. The classic theme switch is disabled, so no classic light-theme verification is claimed. Artifacts are under `/Volumes/Crucial X8/agent-scratch/cwng-promised-2242/`. Portable captures are attached to the Playwright report in CI.

The full local smoke/unit run passed 10,262 tests with 102 skipped and 12 failures: ten old named-preference response expectations were corrected and passed targeted rechecks; two unchanged metadata-replacement SQLite I/O failures reproduce on clean main (the #1734 baseline comparison). CI is the final full-suite gate.

Independent review of the implementation found no blocker in default behavior, account isolation, validation, partial-save handling or authorization. No dependencies or database columns were added.

## Broad CI follow-up

The first broad browser run passed 910 cases with 105 skips and failed the existing reader column-count case on all retries: its `getComputedStyle` measurement received a missing element while epub.js replaced the frame. The measurement now returns unavailable until the root, window and reader container exist. Actual column-width thresholds and saved-preference assertions are unchanged. Independent review approved this narrow harness correction; the actual desktop reader column flow passed a targeted recheck (2 including setup). The immutable-image CI rerun remains the final gate.
