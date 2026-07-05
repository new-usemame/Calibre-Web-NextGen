# #585 v2 — In-SPA sidebar Customize (visibility write + reorder)

v1 (#624, v4.1.4): SPA *honors* the classic `sidebar_view` visibility config (read-only
`me.sidebar` map). Users (@alva-seal, @Glennza1962) then asked for the two remaining pieces:
1. Set visibility **from the new UI** ("can't set these in the new UI yet").
2. **Reorder** entries ("move Shelves up so I don't scroll").

## Approach — a dedicated "Customize sidebar" modal (not free-drag in the live nav)

The live nav is a11y-hardened by #679 (landmarks, focus-trap, `inert`, route focus). Free drag
in it would fight that. Instead: a labeled gear button in the sidebar footer opens a modal that
edits visibility + order; the live nav stays a pure render of the saved settings.

### Customizable entries (14): the 8 browse-by (NAV) + 5 discovery (DISCOVER) + the Shelves block
- Each row: reorder handle + label + visibility toggle (Shelves is always-visible, only movable).
- **Pinned / not customizable** (not part of the ask): Library (top), Upload/Admin, Smart shelves /
  Table / Duplicates, Tasks / About (footer). They keep fixed structural positions.
- Reorder modalities (global rule): up/down buttons (mouse+touch) + keyboard arrows on the focused
  handle (with `aria-live` position announce) as PRIMARY; pointer-drag as progressive enhancement.
  Reset-to-default affordance. No `prefers-reduced-motion` transition.

## Persistence (NO schema change)
- **Visibility** → the existing per-user `sidebar_view` bitmask (same store classic UI + OPDS use;
  one config, so a toggle in the new UI also reflects in classic — that's the intent).
- **Order** → per-user `view_settings["sidebar"]["order"]` (same mechanism as shelf reorder #237),
  a list of stable keys (`author`, `hot`, …, plus `shelves` for the block). Absent → SPA default.

## API (additive)
- **Read:** `serialize_user` already emits `sidebar` (visibility). Add `sidebar_order` =
  `user.get_view_property("sidebar","order") or []`.
- **Write:** one new route `POST /api/v1/account/sidebar` body
  `{ "visibility": {key: bool}, "order": [key, ...] }`. Validate every key against the known set
  (`SIDEBAR_VISIBILITY_BITS` keys + `shelves`); reject unknown/dupes. Flip bits for visibility,
  `set_view_property("sidebar","order", order)` for order, one `ub.session.commit()`. Session +
  CSRF guarded like the other /account mutations. NEW ROUTE → `/security-review`.

## Frontend
- `SidebarCustomize.tsx` modal + `useSidebarSettings` mutation (invalidates `me`).
- `Sidebar.tsx`: render Library (pinned) → customizable entries **in saved order** (nav+discover
  entries and the Shelves block interleaved per `order`; unknown/new keys fall back to natural
  position, stable) → structural footer. Visibility filter already exists (v1); extend to Shelves? No
  — Shelves stays visible.
- i18n: new strings via `_()`/`useT` + anchor SPA-only msgids in `cps/spa_strings.py`.

## Verification (v2)
- Red→green: serializer emits `sidebar_order`; endpoint round-trips + rejects unknown keys + flips
  bits; `Sidebar.tsx`/`SidebarCustomize.tsx` source-pins (order sort + keyboard handlers + toggle).
- Live docker: save via endpoint → reload → SPA renders in order; disabled-then-reordered entry
  stays hidden (v1+v2 compose). Modality matrix: mouse, touch (mobile viewport), keyboard.
- Multi-user: A's order/visibility doesn't leak to B (per-user). Container restart persists.
- `/security-review` (new route). caliBlur × desktop+mobile (default theme deprecated).
