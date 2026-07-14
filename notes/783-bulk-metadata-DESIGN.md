# #783 — bulk metadata maintenance table

## Disposition

Scope: **M for the safe core shipped in this wave; L for the full request.** The SPA already has a read-only, sortable, column-configurable table and the catalog already has permission-gated bulk metadata fan-out for selected cards. The missing medium-sized bridge is fast single-field correction in the table, so this slice adds inline title editing. A spreadsheet that edits authors/series/tags/languages in cells is deliberately deferred: those are relational values requiring canonical suggestions, add-vs-replace semantics, per-row validation, partial-failure recovery, and conflict handling.

## Shipped core

- Everyone retains the read-only sortable table; edit controls render only for `role.edit`.
- Title cells enter an explicit edit mode with a labelled input, Save, Cancel, Enter-to-save, and Escape-to-cancel.
- Writes reuse `POST /api/v1/books/:id/metadata`, the same canonical editor used by Book Detail and the classic inline table; no route or dependency is added.
- Success updates the visible row immediately, errors are announced, and retained catalog caches are invalidated by the existing mutation hook.
- Mouse, keyboard, and touch use the same ≥24px controls. Mobile keeps horizontal table scrolling rather than collapsing fields into unusable one-character columns.

## Full bulk-table follow-up

1. Add row selection to Table using the catalog's existing selection model and `BulkBar`.
2. Offer explicit operations—Add tags, Remove tags, Replace language, Set series—not an ambiguous generic cell overwrite.
3. Fetch canonical typeahead values and show per-row/batch validation before submission.
4. Return structured per-book success/failure results instead of treating `Promise.allSettled` completion as universal success.
5. Add optimistic-concurrency tokens (`last_modified`) so a long-open maintenance sheet cannot silently overwrite a newer edit.
6. Virtualize only after measurement; preserve native table semantics and keyboard navigation.

No external dependency or URL is proposed.
