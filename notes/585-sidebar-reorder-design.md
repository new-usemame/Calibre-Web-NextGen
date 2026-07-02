# #585 — Sidebar reorder (deferred design)

Issue #585 asked for two things in the new UI:

1. **Choose which sidebar entries are visible** — *shipped* in
   `feat/585-sidebar-customize` (v1). The SPA sidebar now honours the existing
   `sidebar_view` bitmask / `check_visibility` config, exposed via a new
   `sidebar` map on `/api/v1/auth/me`.
2. **Reorder entries** (e.g. put Shelves above the discovery views so you don't
   scroll) — *deferred*, designed here.

Reorder is deferred from v1 because it needs new persistence + a drag-and-drop
UI across three input modalities, which is out of scope for a "honour existing
config" fix and shouldn't be shipped half-built.

## Where the order would live (NO new schema)

The `user` table already has a per-user JSON column `view_settings`
(`cps/ub.py`: `view_settings = Column(JSON, default={})`) with
`get_view_property(page, prop)` / `set_view_property(page, prop, value)`
helpers. Fork #237 already uses the same mechanism for **shelf** drag-reorder
(`view_settings["shelves"]["order"]`, applied by
`cps.shelf.sort_shelves_for_user`). Sidebar order should reuse it:

```
view_settings["sidebar"]["order"] = ["library", "author", "hot", "shelves", ...]
```

- Keys are the same stable, UI-agnostic identifiers introduced in v1
  (`SIDEBAR_VISIBILITY_BITS` in `cps/api/serializers.py`), plus non-bit
  pseudo-entries the SPA renders (`library`, `shelves`, `upload`, `admin`,
  `tasks`, `about`, `table`, `magic`) if we want those movable too — start with
  just the visibility-bit entries + `shelves` to keep v2 small.
- No column, no migration: `view_settings` already exists and is already
  null-normalised (`migrate_user_view_settings_null`). Per-user by construction,
  so it satisfies "reorder MY sidebar" without affecting other users.

## API shape (additive)

- **Read:** fold the order into the same `/api/v1/auth/me` payload the v1
  visibility map ships in — add `"sidebar_order": user.get_view_property(
  "sidebar", "order") or []`. Empty/absent → SPA uses its default order.
- **Write:** one new endpoint, e.g. `PATCH /api/v1/account/sidebar-order` with
  body `{ "order": ["library", "hot", ...] }`. Validate every element is a known
  key (reject unknown/dupes), then
  `current_user.set_view_property("sidebar", "order", order)` +
  `ub.session_commit()`. CSRF/session-guarded like the other account mutations.
  This is a **new route**, so it triggers `/security-review` per CLAUDE.md.

## Frontend (SPA)

- `Sidebar.tsx`: after filtering by visibility (v1), sort the combined entry
  list by the stored order; entries not in the stored order fall back to their
  natural position (stable). Persist a merged "known keys in stored order, then
  the rest" so newly added nav entries don't vanish.
- Drag-and-drop must cover **all three modalities** (global rule):
  - **Mouse/touch:** native HTML5 drag events are unreliable on touch. Prefer a
    pointer-events-based reorder (pointerdown/move/up) written in plain TS — no
    new dependency (constraint: no new deps). The shelf-reorder code (#237) may
    already have a reusable helper; check `frontend/src` before writing new.
  - **Keyboard:** each handle needs `role="button"`, arrow-key move
    (Up/Down = move item), and an `aria-live` announcement of the new position.
  - **Reduced motion:** no transition animation when
    `prefers-reduced-motion: reduce`.
- Provide a "Reset to default order" affordance (clears
  `view_settings.sidebar.order`).
- Debounce the PATCH (e.g. commit order on drop, not per pointermove).

## Verification checklist for v2

- Red test: `get_view_property("sidebar","order")` round-trips; PATCH endpoint
  rejects unknown keys; `Sidebar.tsx` source-pins the sort + keyboard handlers.
- Live: set an order via the endpoint, reload, confirm the SPA renders in that
  order; confirm a disabled-then-reordered entry stays hidden (v1 + v2 compose).
- Modality matrix: mouse drag, touch drag (mobile viewport), keyboard move.
- Multi-user: user A's order doesn't leak to user B (per-user `view_settings`).
- Container restart: order persists (it's a DB column, so this is a smoke check).

## Scope estimate

Small-to-medium: ~1 new endpoint + validation, ~1 `get_view_property` read in
the serializer, and the DnD component work (the bulk). No schema, no new deps,
no new env vars. Keep it its own PR so the DnD surface gets `/security-review`
(new route) + a proper modality pass.
