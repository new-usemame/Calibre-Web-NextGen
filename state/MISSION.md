# Mission: SPA web-reader phase 1

## Definition of done

- [x] Project authority, briefing, designs, and BOSS-NOTES consumed.
- [x] Current classic reader, SPA reader, progress, preferences, and annotation read flow audited.
- [x] `notes/org/SPA-audit.md` delivered before implementation.
- [x] Design drift and any phase-1 scope change recorded in `notes/org/SPA-REPORT.md`.
- [x] Settings slice implemented and regression tested.
- [x] Unsafe position save/restore removed; coordinated Phase 2 design delivered.
- [x] Independent TOC accessibility slice tested; uncertain navigation enhancement removed.
- [x] SPA strings anchored; no new dependencies; symptom-first CHANGELOG entry added.
- [x] Reader WCAG 2.2 AA automated gates green, including six-theme reader chrome sweep.
- [x] Dedicated `cwn-spA` stack on port 8101 live-verified with a real EPUB.
- [x] Luna verified appearance persistence at 375px + desktop and Light + Dark; position restore intentionally excluded.
- [x] Terra re-verified the cleaned shipped slice PASS.
- [x] PR #889 pushed and opened with evidence; no merge performed.
- [x] Phase-2 remainder honestly recorded; report evidence classified OBSERVED or ASSUMED.

## Current state

- Phase: PR handoff
- Branch: `org/spA`
- Next action: Opus boss review/merge of PR #889.

## Decisions

- 2026-07-13: use ALEX-DEV-capabilities, CWNG-consume-briefing, ALEX-DEV-OPUS-run-to-done, ALEX-ORCHESTRATE-model-routing, CWNG_a11y, and ALEX-UI-PILOT-browser.
- 2026-07-13: preserve SPB fence; annotation authoring and kosync/annotation backend edits excluded unless coordinated through BOSS-NOTES.

## Evidence

- OBSERVED: operator supplied scope, lifecycle, branch, runtime isolation, delegation, and merge fence.
- OBSERVED: focused tests, production build, cwn-spA E2E/a11y, Luna real-EPUB flow, and Terra PASS are recorded in notes/org/SPA-REPORT.md.
