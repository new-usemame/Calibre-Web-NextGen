# Migration Log

Tracking the custom-column hierarchy value-editing feature migration from
`Calibre-web-NextGen02-ui-test` (branch `recovery/cc-hierarchy-stash`,
HEAD `90098fba9`) into `Calibre-web-NextGen02-migration` (branch
`feature/cc-hierarchy-migration`).

Each entry records the phase, the files touched, and line ranges (from -> to).
Line numbers refer to the target file **after** the change unless noted.
Line counts are the reliable `(Get-Content <file>).Count`.

---

## Phase 1 — Sidecar tables + reconcile

- **New file** `cps/cc_sidecar.py` (643 lines): copied verbatim from
  source `cps/cc_sidecar.py` (byte-identical, SHA-256
  `763FF0843F02433B121E618E49EF11F26289F56E9BDF2F6DCFD17BBA03BD0538`).
  Provides `ensure_tables()`, `reconcile()`, `stored_order()`,
  `sibling_order()`, `set_order_for_siblings()`, `drop_order()`,
  `carry_order()`, `set_hierarchy_verdict()`, `hierarchy_verdict()`,
  `parse_column_id()`, and the `cc_column` / `cc_value_order` tables
  (plus `ix_cc_value_order_sibling`). Reached through `CalibreDB`'s
  session so both `app.db` and `metadata.db` are attached.
- **Modified** `cps/__init__.py` (895 -> 909 lines): inserted lines
  620-632 (the sidecar reconcile block) immediately after the
  custom-column visibility seed (lines 614-618) and before
  `updater_thread.init_updater` (now line 634), inside
  `if first_process_initialization:` and after `calibre_db.init_db()`
  (line 591). Mirrors source `cps/__init__.py` lines 508-520. The
  block is wrapped in a non-fatal try/except (a missing sidecar costs
  the curated order and nothing else).

Verification: `from cps import cc_sidecar` resolves from the target
dir; `reconcile` is callable; full `import cps` smoke test passes;
`tests/unit/test_custom_column_calendar_dates.py` 11 passed.

---

## Phase 2 — Order-aware read path

- **Modified** `cps/hierarchy.py` (145 -> 224 lines, +79): added the
  `UNORDERED` sort sentinel and `_order_key(order, node)` (curated
  position first, then name); `parse_tag_hierarchy(values, order=None)`
  now accepts any 2-element sequence (SQLAlchemy 2.x `Row`, not just
  `tuple`) and threads `order` through `_finalize`; `_finalize(nodes,
  order=None)` sorts siblings by `_order_key`; new
  `add_bookless_nodes(nodes, values, order=None)` inserts values no
  visible book carries as count-0 nodes (`total_count` deliberately NOT
  propagated upward). Lines 1-203 are now byte-identical to the source;
  the only remaining diff is Phase 3's node-manipulation rules section.
- **Modified** `cps/db.py` (2863 -> 2914 lines, +51):
  `get_hierarchical_tree` now runs two queries (book-filtered
  `(id, value)` rows + an unfiltered `distinct` value list), fetches
  `cc_sidecar.stored_order(col_id)` via a local import (avoids the
  import cycle, since `cc_sidecar` imports `db`), parses with `order`,
  then merges bookless values via `add_bookless_nodes`.
  `get_cc_flat_list` likewise runs a count query + an unfiltered
  distinct-value query, builds entries from every value (count 0 when a
  book carries none), and sorts by
  `hierarchy._order_key(cc_sidecar.stored_order(col_id), e)`. Both
  methods are byte-identical to the source.
- **Modified** `tests/unit/test_flat_custom_columns.py` (233 -> 278
  lines): the `_flat_entries` helper and
  `test_the_flat_list_honours_a_book_filter` now stub the two-query
  pattern (a count query and a distinct-value query, dispatched by call
  order). Copied verbatim from the source (byte-identical).

Verification: `get_hierarchical_tree` + `get_cc_flat_list`
byte-identical to source; `import cps` OK; 143 tests pass across
test_hierarchy, test_hierarchy_template, test_flat_custom_columns,
test_tag_hierarchy_browse, test_tag_hierarchy_catalog,
test_2170_hierarchical_columns, test_custom_column_calendar_dates,
test_cc_visibility_seeding, test_custom_column_sort,
test_1233_custom_column_none_sentinel.

---

## Phase 3 — Node-manipulation rules + published move flags

- **Modified** `cps/hierarchy.py` (224 -> 518 lines, +294):
  added the node-manipulation rules section -- `ERR_*` refusal
  codes, `is_descendant`, `can_place_under`, `relocated_path`,
  `plan_rename`, `plan_move`, `merge_sources`, `plan_merge`,
  `plan_delete`, `plan_reorder`, and `node_availability`
  (publishing can_delete / can_merge_source / can_move_up /
  can_move_down, each with its `_reason` code). Pure functions:
  no DB, no Flask, no gettext. Now byte-identical to the source.
- **Modified** `cps/api/columns.py` (221 -> 247 lines):
  `_node_to_json(node, siblings=None)` now publishes
  `"available": hierarchy.node_availability(node, [...])`;
  `column_tree` passes the whole `nodes` list as each node's
  siblings.
- **Modified** `cps/web.py` (4556 -> 4597 lines): added
  `cc_value_edit_rows(entries, is_hierarchical)`, which flattens
  the browse tree into editor rows, each carrying the same
  `node_availability` flags the JSON tree publishes.
- **New test files** (copied verbatim): `test_cc_reorder_rules.py`
  (304 lines), `test_hierarchy_node_rules.py` (616 lines).

Verification: 74 tests pass across those two files; the classic
rows and the JSON tree agree on the move flags
(test_the_classic_rows_and_the_json_tree_agree_about_moving).

---

## Phase 4 — Value-editing API + reorder endpoint + five-way order maintenance

- **New file** `cps/api/cc_values.py` (901 lines): copied
  verbatim from the source (byte-identical). Six POST endpoints
  on `api_v1`:
      POST /api/v1/columns/<id>/values/rename  -> rename_cc_value
      POST /api/v1/columns/<id>/values/reorder -> reorder_cc_value
      POST /api/v1/columns/<id>/values/merge   -> merge_cc_values
      POST /api/v1/columns/<id>/values/delete  -> delete_cc_values
      POST /api/v1/columns/<id>/values/move    -> move_cc_value
      POST /api/v1/columns/<id>/values/create  -> create_cc_value
  plus the five-way order-maintenance helpers `_carry_order`,
  `_drop_order`, `_set_sibling_order` (rename carries a curated
  position; move/merge/delete drop it; create writes none) and the
  shared planners `_plan_rename` / `_plan_move` that delegate to
  `hierarchy.plan_rename` / `hierarchy.plan_move`.
- **Modified** `cps/api/__init__.py` (238 -> 239 lines):
  registered the module -- `from . import cc_values` -- immediately
  after `columns`, matching the source.
- **New test files** (copied verbatim): `test_cc_reorder_endpoint.py`
  (332 lines), `test_cc_value_bookless.py` (589 lines),
  `test_cc_value_logging.py` (204 lines).
- **Also migrated (belong to earlier phases, added late):**
  `test_cc_sidecar.py` (428 lines, Phase 1) and
  `test_cc_order_read_path.py` (227 lines, Phase 2).

Verification: `from cps.api import cc_values` imports cleanly; all
six route functions present; 157 tests pass across the seven
Phase 1-4 test files; a 146-test regression sweep (API + custom
column + hierarchy + tag-hierarchy suites) still passes.

---

## Phase 5 — Both clients (classic editor + SPA editor, narrowing,
create-beside, density)

- **Already in place (byte-identical to source, SHA-256 verified):**
  `cps/static/js/cc_value_editor.js` (669 lines),
  `cps/templates/cc_value_editor.html` (163),
  `cps/templates/cc_list.html` (identical; includes
  `cc_value_editor.html` at line 56 and loads the JS at line 63),
  `frontend/src/pages/CcValueEditor.tsx` (732),
  `frontend/src/components/CcValuePicker.tsx` (207),
  `frontend/src/lib/ccNarrow.ts` (67).
- **Classic client server side (already in place, verified equal):**
  `cps/web.py` — the `cc_list` root view passes
  `may_edit_values` and `edit_rows=cc_value_edit_rows(...)`
  (lines 2957-2958); `cc_value_edit_rows` (2960+) flattens the
  tree into `(node, depth)` rows carrying the same
  `node_availability` flags the JSON tree publishes.
- **New files copied verbatim:**
  `frontend/src/lib/ccPaste.ts` (81 lines),
  `frontend/src/lib/ccMerge.ts` (72),
  `frontend/src/pages/CcValueEditor.module.css` (175),
  `frontend/src/components/CcValuePicker.module.css` (77),
  `frontend/unit/ccCycleCases.json` (57), and the seven unit
  tests `ccBrowseDisclosureSize.test.ts` (140),
  `ccEditorNarrowing.test.ts` (181), `ccMerge.test.ts` (113),
  `ccNodeAvailability.test.ts` (123), `ccValueEditorGate.test.ts`
  (88), `ccValuePaste.test.ts` (129), `ccValuePasteParity.test.ts`
  (91). (`frontend/src/lib/a11y/announcer.tsx`, a dependency of
  the editor, was already identical in both repos.)
- **Copied verbatim — source is a strict superset:**
  `frontend/src/pages/CcBrowse.tsx` (337 -> 370): adds the
  `Pencil` import, the `CcValueEditor` import, the `editing`
  state, the `containerFull` width class, and the Edit/Done toggle
  gated on `me?.role?.edit`; the Books-placement toggle, the
  `afterChildren` state and the hint survive inside
  `{!editing && (...)}` wrappers; the edit-mode branch renders
  `<CcValueEditor colId hierarchical onDone>`.
  `frontend/src/pages/CcBrowse.module.css` (316 -> 370): adds
  `.containerFull` and `.editToggle`; the deliberate density change
  (tree row 44px -> 28px: `padding: 0 var(--sp-3)`,
  `min-height: 28px`, disclosure 28x28) so browse rows and editor
  rows are the same height. Horizontal padding stays `--sp-3`
  because `.children` anchors its indent on it.
- **Surgical merge** `frontend/src/lib/api.ts` (1215 -> 1272,
  +57): added `CcNodeAvailability` (367-389),
  `available?: CcNodeAvailability` on `CcNode` (396, doc comment
  extended), `CcValueConflict` (416-421), `CcValueWriteResult`
  (423-437), and `hierarchical?: boolean` on
  `EditableCustomColumn` (655-661). Every target-only newer field
  is preserved: `Me.catalog.custom_field_ids/custom_field_labels`,
  `ReadingStatus`, `Book.personal_rating/household_rating/
  favorited/read_status/custom_columns`,
  `ListCustomColumnDefinition`, `BookDetail.personal_rating/
  household_rating/read_status`, `BooksPage.sort/
  sort_persistable/custom_sort_options/
  custom_column_definitions`, `ShelfDetail.custom_column_definitions`,
  the extended `AdvancedSearchParams.read_status` union,
  `AdvSearchResult.sort/...`, `TaskItem.error`, the `TASK_*`
  constants, `apiPostDownload`'s `signal`, and `apiUpload`'s
  `method` parameter.
- **Surgical merge** `frontend/src/lib/queries.ts` (2128 -> 2226,
  +98): `CcValueWriteResult` added to the api type import (line
  30); the value-editing block inserted after `useCcTree`
  (561-660): `invalidateCcViews` (572-579), `RenameCcValueInput`
  (580-585), `useRenameCcValue` (586-599), `useMergeCcValues`
  (600-608), `useDeleteCcValues` (609-617), `useMoveCcValue`
  (618-634), `useReorderCcValue` (635-644), `useCreateCcValue`
  (645-660, sends `after` for create-beside). All target-only
  newer hooks preserved (useDiscoverSource/useSaveDiscoverSource,
  useUpdateCatalogCustomFields, owner-captured named preferences,
  the `personal_rating` entity kind, `tagPath`, bookCoverCache
  refresh, useShelfAvailableBooks, useReaderFonts,
  useConversionTask, nested magic-rule values, and the rest).
  Placement note: the source left the `useCcBooks` doc comment
  orphaned above the inserted block; the target inserts the block
  between `useCcTree` and that comment so it stays attached to
  `useCcBooks`. Functionally identical.

Verification: `npm run test:unit` 294 passed (including the seven
new cc tests); `npm run build` (`tsc -b && vite build`) clean,
2021 modules, bundle written to `cps/static/app/`; Python: 300
passed across the 17 migration/regression files; API v1 sweep
164 passed plus one pre-existing Windows-locale failure
(`test_api_v1_edit.py::test_classic_delete_modal_...` reads
`main.js` with the cp1252 default codec; passes with
`PYTHONUTF8=1`; `main.js` is untouched by this migration).

---

## Phase 6 — Stored hierarchy verdict becomes authoritative
(tri-state override + admin UI)

- **Modified** `cps/db.py` (2914 -> 2933, +19):
  `is_flat_cc_column` (2489-2511) now consults
  `cc_sidecar.hierarchy_verdict(col_id)` FIRST (local import,
  since `cc_sidecar` imports `calibre_db` and `db`); a stored
  `True`/`False` verdict wins outright (`return not
  verdict.value`), and only a `None` verdict falls through to
  the data-derived `get_hierarchical_column_ids`. With an
  override in place the derivation — and its 300-second cache —
  is never consulted.
- **Modified** `cps/admin.py` (4188 -> 4258, +70):
  - `from .custom_column_visibility import load_browsable_columns,
    save_cc_visibility` (line 47) and `from . import cc_sidecar`
    (line 75).
  - `view_configuration` (781): builds `cc_modes` (790-804) —
    every browsable column with its stored verdict, passed as the
    string `auto`/`hierarchical`/`flat` so the tri-state stays
    distinguishable — and renders it as `ccModes=cc_modes` (811).
  - New `_save_cc_hierarchy_verdicts` (1147-1190): scans
    `request.form` for `cc_hierarchy_mode_<id>` fields, parses the
    attacker-controlled id with `cc_sidecar.parse_column_id`
    (bounded by the sidecar's own `_MAX_COLUMN_ID`), maps
    `hierarchical`->True / `flat`->False / `auto`|""->None, and
    writes through `cc_sidecar.set_hierarchy_verdict` (which also
    drops the cached derivation). Absent fields mean "not
    mentioned", never "clear it".
  - `update_view_configuration` (1196): writes the verdicts
    (1199-1203) before any settings validation, so a failed
    settings save cannot roll them back.
- **Modified** `cps/templates/config_view_edit.html` (334 -> 351,
  +17): the "Custom columns" explanation becomes
  `{{_('Magic Shelves')}} — {{_('Sort by')}}` (line 78, part of
  the source checkpoint), and the per-column tri-state select
  block (91-106) is inserted directly after the sortable-columns
  control — `<select name="cc_hierarchy_mode_{{ mode.id }}">`
  with `auto` / `hierarchical` / `flat` options, gated on
  `{% if ccModes %}`. The target-only OPDS Downloads section is
  preserved; the template now differs from the source only by
  that section.
- **New test file** (copied verbatim):
  `tests/unit/test_cc_hierarchy_override.py` (345 lines, 21
  tests) — covers all six stored-verdict/data combinations, the
  "derivation not even consulted when a verdict exists" assertion
  (`calls == 0`), the malformed-id and unknown-mode refusals,
  and the absent-field-means-untouched rule.

Verification: 21/21 Phase 6 tests pass; 321 passed across the
18 migration/regression files (Phases 1-6); 20 passed across the
admin/config suites that exercise `view_configuration` /
`update_view_configuration` (test_custom_sort_admin_persistence,
test_classic_boolean_restriction_admin,
test_463_css_banner_ui_config_placement,
test_view_settings_null_migration); `config_view_edit.html`
parses as valid Jinja2 and the `ccModes` block is present.

---

## Phase 7 — Strings, translations, docs

- **Regenerated** `cps/spa_strings.py` (2220 -> 2235 lines)
  with `python scripts/extract_spa_strings.py --write`:
  the AUTOGEN block (680-2235) now anchors 1548 msgids,
  including the 80 new editor strings Phase 5 introduced
  (`1 level`, `Create new`, `A value cannot be pasted into
  itself`, ...). The regeneration also repaired pre-existing
  drift the stale block had masked: the frontend's
  discover-source feature had renamed
  `A few random picks from your library` to
  `A few random picks from your Discover source`
  (`frontend/src/components/DiscoverSection.tsx:54`), and
  the AUTOGEN block had not been regenerated since.
- **Fixed the stale pin** `tests/unit/test_spa_strings_anchored.py`
  (line 105): `test_reporter_menu_strings_anchored` pinned
  the old `...from your library` string; updated to the
  string the frontend actually renders. Pre-existing
  inconsistency, exposed (not caused) by the regeneration.
- **New changelog fragment** `changelog.d/
  custom-column-value-editing.md` (house convention:
  `*.md` is gitignored, so it needs `git add -f` like the
  other fragments): two `### Added` entries — value editing
  in both clients, and the per-column hierarchy mode.
- **Design documents** (`cc_sidecar_datamodel.md`,
  `cc_sidecar_implementation*.md`,
  `hierarchy_*_implementation.md`, `hierarchy_logging.md`,
  `hierarchy_up_down_analysis.md`) are gitignored working
  documents in the source repo (the repo's `.gitignore`
  blanket-ignores `*.md`); per the plan they are committed
  only "if they are to be committed" — they are not, so
  they stay uncommitted, as in the source.
- **Translation catalogs deliberately NOT regenerated**: the
  source's own feature branch left `messages.pot` and the
  28 `messages.po` files untouched (verified: neither repo's
  catalog contains any feature string); regeneration is the
  translation bot's "Update translations" job after merge.
  The SPA anchoring mechanism is what makes the strings
  extractable, and that is now in place.

Verification: `tests/unit/test_spa_strings_anchored.py`
46/46 (was 45/46 with 80 unanchored keys before the
extractor run); `tests/unit/test_changelog_diff_guard.py`
35/35; `test_po_source_location_churn.py` and
`test_i18n_format_flags.py` pass; frontend `npm run
test:unit` 294/294; the combined Phase 1-7 sweep is
**465 passed, 30 skipped** across the 22 migration,
string-gate and translation-invariant files.

Live boot (fresh config, no library configured):
`GET /` and `GET /admin/viewconfig` both 200 on
127.0.0.1:8083; the sidecar tables `cc_column` and
`cc_value_order` plus `ix_cc_value_order_sibling` were
created in app.db during boot, and the reconcile failure
on the unconfigured Calibre session was caught by the
Phase 1 non-fatal try/except (logged, app continues) —
exactly the designed degradation.

(End of migration — all eight phases complete:
Phase 0 baseline, 1 sidecar, 2 read path, 3 rules,
4 API, 5 both clients, 6 override, 7 strings/docs.)
