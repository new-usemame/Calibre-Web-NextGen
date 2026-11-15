# Shelf management verification (#1734)

The ordinary and smart-shelf overview → create → settings workflows were exercised against an image built from this branch in a private container. Tests create their own viewer account and shelves, verify persisted visibility, and remove owned test records. The viewer can share their own shelves without permission to edit public shelves belonging to other people.

## Decisions

- Give both shelf types explicit creation and settings pages. Keep ordinary tiles lightweight with counts; smart tiles identify built-in/custom and hidden status. Cover mosaics would require extra book queries for every shelf and were optional in the request, so they are deferred.
- Default the new own-shelf-sharing permission on for existing/new accounts. It is independent from editing other people's public shelves. Owners may always make their own public shelf private after that permission is revoked.
- Keep built-in smart shelves private, matching the classic editor; reject direct attempts to change their visibility.
- Keep device marks owner-only. OPDS exposure remains an account preference, and omitted fields preserve existing exposure during partial saves.
- Restore hidden built-in/shared smart shelves from the overview using the existing classic per-user preferences. Keep custom own shelves visible; independently collapsing each sidebar list handles long personal lists.
- Keep the two shelf groups adjacent within the customizable navigation block. Collapse state persists locally across reloads and works from the keyboard.

## Evidence

Before implementation, the real overview/create flow failed on the main image because the overview and creation links were missing. Three API management/visibility tests failed without the added routes. An OPDS mutation that forced the created preference off was caught by its persistence test.

The final targeted backend run passed 137 tests. Frontend unit tests passed 192 tests. Vite/TypeScript builds passed. The focused real desktop/phone workflows and accessibility scans cover both themes; nested rules and canonical rule-schema tests also passed. The complete backend smoke/unit suite and CI results are recorded in the PR.

Independent review found two UI/server permission mismatches. Both were corrected and independently rechecked: system shelf sharing and non-owner admin visibility changes. The final review found no remaining blocker. A separate static security review found no exploitable finding at its reporting threshold; its Guest ownership caveat prompted an explicit anonymous edit guard and behavioral test.

JPEGs are generated and attached by `frontend/e2e/shelf-management.spec.ts`: 1280px desktop and 375px phone, New UI light/dark, classic caliBlur. The classic theme switch is disabled in this codebase, so the classic screenshots only claim the served caliBlur theme. Local evidence/logs are retained under `/Volumes/Crucial X8/agent-scratch/cwng-promised-1734/`; CI publishes Playwright reports with the same captures.

No Kobo device was used: the change is to shelf configuration, ownership and web UI. Real device sync behavior is outside this PR's verification claim.

The first broad CI run found an older book-menu test expecting public-shelf owners to lose access when their cross-account edit role was removed. Updating that behavioral expectation exposed a real frontend/classic parity gap: those book controls still applied the old public-role-only policy. Their shared New UI predicate and classic templates now deny Guests first and allow signed-in owners, while non-owners still need the public edit role. The owner/public frontend test and classic rendered-template test were both seen red before these fixes. Independent review found no blocker in the follow-up. Frontend units and focused classic checks passed; final desktop/phone management, drawer and book-offer flows passed 14 tests with 1 existing desktop skip before the additional classic-owned-public scenario, which passed in the separate 5-test desktop/phone recheck (including setup).

Six additional desktop/phone JPEGs show the corrected classic removal menu and New UI single-book/bulk offers. The final capture run passed all five tests including setup; there are 26 named captures in the local evidence directory.
