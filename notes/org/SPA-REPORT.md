# SPA web-reader Phase 1 report

## Shipment state

- **Appearance settings: READY.** Per-user page theme, font family, font size, page margins, and line height persist through the dedicated `/api/v1/reader/settings` store in `User.view_settings`. The SPA hydrates the complete settings snapshot before creating the rendition; the classic reader and SPA share canonical validation/partial-merge behavior.
- **TOC: READY. Navigation enhancement: DESCOPED.** The TOC focus/list/close improvements are client-only and independent of position storage; Luna observed a real chapter activation. The interrupted prototype's new PageUp/PageDown/Space/touch-swipe implementation did not produce a Luna-observable rendition delta, so it was removed. The pre-existing ArrowLeft/ArrowRight and page-button implementation is unchanged.
- **Position save/restore: DESCOPED.** The unsafe KOSync-coupled prototype was removed in full. No changed production file reads/writes `KOSyncProgress`, changes `cps/progress_syncing/`, adds bookmark progress columns, or sends percentage/device/revision fields. The existing private CFI bookmark behavior is unchanged. Coordinated Phase 2 design: `notes/SPA-position-sync-DESIGN.md`.

## Verification evidence

- **OBSERVED — focused backend/parity green:** settings API, sanitizer, SPA-string anchoring, classic modal, dedicated-route, line-height, and font-range checks → **35 passed**.
- **OBSERVED — red baseline:** `origin/main` has no `/api/v1/reader/settings` route, no SPA settings query/mutation, and no `lineHeight` reader-setting contract; the new regression assertions target those missing behaviors. The interrupted session did not preserve the original failing pytest transcript, so that transcript itself is **ASSUMED**, not claimed observed.
- **OBSERVED — production build:** `frontend/npm run build` → TypeScript + Vite success, **1,877 modules transformed**.
- **OBSERVED — isolated live stack:** image rebuilt from `org/spA`; compose project `cwn-spa`, container `cwn-spA`, host `8101`; health returned `200 {"api":"v1","status":"ok"}`. `cwn-local`/8086 was not used.
- **OBSERVED — authenticated reader E2E on 8101:** `E2E_BASE_URL=http://localhost:8101 npm run test:e2e -- reader-phase1` → **4 passed, 1 intentional duplicate mobile theme sweep skipped**. Desktop and 375×667 both changed all five controls, polled the persisted server snapshot, reloaded, and asserted exact values. Reader chrome had no critical/serious axe findings across all six app themes.
- **OBSERVED — broader a11y gate on 8101:** `E2E_BASE_URL=http://localhost:8101 npm run test:e2e -- a11y` → **20 passed, 9 matrix skips**. The reader-specific spec supplies authenticated reader coverage where the generic reader fixture skipped.
- **OBSERVED — Luna real EPUB:** authenticated to `cwn-spA`, opened *The Adventures of Sherlock Holmes* (book 192); desktop changed Sepia/SimSun/160%/0px/100% and reload retained all five; a second desktop pass retained Dark/Arial/130%/24px/160%. Appearance focus entered/trapped, Escape closed, and focus restored. Dark app chrome and readable dark reader content were observed.
- **OBSERVED — Luna mobile:** at exactly 375×667 the appearance panel measured `x=30, y=0, width=345, height=667`, entirely inside the viewport. Light/Dark passes changed all five controls; reload reopened with a persisted non-default snapshot. The real EPUB remained rendered.
- **OBSERVED — Luna TOC:** the real chapter list opened and activating “I. A SCANDAL IN BOHEMIA” navigated to the canonical OEBPS chapter with visible text. The failed experimental keyboard/touch navigation probe caused that enhancement to be removed from the PR.
- **OBSERVED — Terra final re-verification:** `notes/org/SPA-REPORT/terra/REVERIFY.md` verdict **PASS** after resolving all three parity blockers: dedicated route ownership, classic line-height wiring, and the shared 75–200 font range. Navigation enhancement descope also passed.

## Terra F1–F5 disposition

1. **F1 incompatible locator:** removed from shipment; no EPUB CFI is written to KOSync.
2. **F2 restore-before-locations-ready:** no new cross-engine percentage restore ships; Phase 2 ordering is specified in the design.
3. **F3 arrival-order race / uniqueness:** no new shared save protocol ships; CAS/transaction and `(user_id, document)` uniqueness are Phase 2 requirements.
4. **F4 non-atomic invalid payload:** unsafe combined payload handling was removed; full-body validation before mutation is a Phase 2 requirement.
5. **F5 best-effort cross-device winner:** no cross-device web winner logic ships; durable private-CFI/common-percentage arbitration is Phase 2.

## PR and phase-2 gap

- Appearance + independent TOC accessibility PR: **[#889](https://github.com/new-usemame/Calibre-Web-NextGen/pull/889)**.
- Phase 2 deliberately remains unimplemented until SPA and SPB coordinate the common percentage carrier, private engine locators, atomic ordered saves, uniqueness migration, validation, and restore ordering. This run does not claim cross-device position sync.
