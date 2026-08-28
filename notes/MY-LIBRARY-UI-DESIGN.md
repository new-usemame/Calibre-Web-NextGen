# My Library — UI design (SPA + classic)

Design for the per-user library membership feature (issue #1939). **Design only** — no feature
code here. The implementing lane should treat this as the UI contract: placement, copy, and
states are decided; endpoint shapes are noted where the UI depends on them.

**Revision 2** — incorporates five operator rulings: a first-run introduction card (§3),
monolibrary mode as a first-class named mode with a user-facing switch (§4), the migration
seed ("every existing user wakes up owning exactly what they can see today", §4.5), shelves
demoted from headline copy to secondary detail (pass over §0, §6, §7), and `added_by` dropped
from v1 (no UI anywhere depends on it; §11).

Reading key: file references are to `repo/frontend/src/…` (SPA) and `repo/cps/templates/…`
(classic). "Owned" / "unowned" below always means *in the current user's membership set* —
never file ownership.

---

## 0. The mental model the UI must teach

Two sentences, repeated by every surface:

> **Your account and the library are different things.** The library is shared — every book
> lives in it once. Your account keeps its own selection of it.

> **You can do book things with books in your library. The one thing you can do with a book
> outside it is add it.**

And one named spectrum, which is how a reader experiences the feature (operator ruling: the
mode is a spectrum, not a checkbox — no user should ever have to reason about a database
flag):

- **My selection** — your library holds only the books you picked.
- **The whole library** — your library is everything on the server, new books included.

The whole feature is one flag (`has_own_library`) with two named states; §4 designs the
switch. Everything in §5–§9 describes **selection mode**; §4.4 lists what whole-library mode
suppresses.

Everything hangs off those ideas:

- `/` (the catalog) is **My Library** in selection mode. It looks and behaves exactly like
  today's catalog — because for that user, it *is* the library.
- `/global` is the **Global Library** (selection mode only): the whole archive, where
  unowned books offer **Add to my library** and owned books read as already yours.
- **Remove from my library** is a personal, reversible action. It lives with the other
  personal actions (Favorite, Archive, Hide) and confirms in plain language.
- **Delete from the global library** is an administrative, irreversible act. It stays in the
  danger zone, and its copy now says "for every member", because in a membership world the
  old text ("your library") becomes false.

Vocabulary is fixed and used identically in both themes (the SPA and classic share the same
`.po`-derived catalogs, so identical English source strings translate once):

- Actions speak from the user's hand: **My Library**, **My selection**, **Add to my library**,
  **Remove from my library**.
- State is told to the user: **The whole library**, **In your library**, **Not in your
  library**, "your e-reader".
- The words "monolibrary", "entitlement", "membership", and `has_own_library` never appear in
  any user-visible string. "Sync" appears only inside "the next time your e-reader updates"
  phrasing — the device-visible event, not the mechanism. The §4.6 test audits every string
  against this.

---

## 1. Data contract the UI needs

Shapes, not endpoints — the lane picks routes; these fields must exist in the payloads:

```ts
// Me (lib/api.ts) — role is already Record<string, boolean>
me.has_own_library: boolean            // THE mode flag: 1 = "My selection", 0 = "The whole
                                       // library". Absent on old servers ⇒ 0 (whole).
me.role.browse_global: boolean         // new role bit (1<<9)
me.library_book_count?: number         // size of the user's selection — live or dormant.
                                       // Powers the {count} in the mode-switch copy (§4.2)
                                       // and the admin seeded-count banner.

// Book (lib/api.ts) — list + detail serializers
book.in_library?: boolean              // present when the requester is in selection mode;
                                       // ABSENT ⇒ treat as owned (same back-compat rule
                                       // as `hidden?: boolean` on the list item today)

// Admin user payload: has_own_library + library_book_count per row.
// Mode-switch response (user or admin initiated):
{ has_own_library: boolean, selection_count: number, seeded: boolean }
//   seeded=true only when the switch CREATED the selection (first time ever);
//   seeded=false means the dormant rows were restored — the two confirms differ (§4.2).
```

**Mode flips never delete membership rows** (operator ruling: no data loss of what the
user's preferences were). Switching to the whole library leaves the selection dormant —
rows, order and timestamps intact — so switching back restores it exactly. This is a load-
bearing guarantee: the §4.2 copy *promises* it, and the promise is only safe to ship if the
backend keeps it.

**`added_by` is dropped from v1** (operator ruling). No UI in this document reads it; the
schema decision is the lane's, but no surface reserves space for "who added this".

Pagination note that decides two controls below: the catalog grid paginates server-side, so
the `/global` scope segments and the text filter are **query parameters**
(`scope=all|unowned`, `q=…`), not client filters. (`BrowseList`'s client-side filter works
only because entity lists are unpaginated — do not copy that pattern here.)

The remove-confirm needs the book's shelf names — already available client-side via the
existing `useBookShelves(bookId)` membership query (`BookDetail.tsx:287` reads it with no
extra request).

---

## 2. The Global Library section

### 2.1 Route and navigation

- New route `global: '/global'` in `lib/routes.ts`.
- Sidebar: a **pinned** entry, second position, directly under the root `Library` item
  (`components/Sidebar.tsx:271-284`), in the same first `<ul>`. It is **not** added to
  `ORDERABLE_ENTRIES` (`lib/sidebarEntries.ts`) — orderable entries can be hidden by the user
  and are not role-conditional; a user must never be able to hide their only way back to
  adding books.
- Visibility: **selection mode AND `me.role.browse_global`**. Both conditions — in
  whole-library mode your library already *is* the global library (§4.4), and the role gates
  access.
- Icon: `Globe` (lucide). It already means "public/global scope" in `AddToShelf.tsx` (public
  shelf marker), so the metaphor is consistent. (`LibraryBig` is the fallback if the reuse
  reads as a collision in review.)
- Active state: prefix match, same `isActive(location, '/global')` helper.
- The root entry's label switches with the mode — the single most important orientation cue
  in the whole feature: **"My Library"** in selection mode, **"Library"** (existing msgid,
  zero churn) in whole-library mode.

```tsx
// Sidebar.tsx — pinned first <ul>
<Link href="/" className={isActive(location, '/', true) ? styles.itemActive : styles.item}
      aria-current={isActive(location, '/', true) ? 'page' : undefined} onClick={onNavigate}>
  <Library size={18} className={styles.icon} aria-hidden="true" focusable={false} />
  <span>{t(me?.has_own_library ? 'My Library' : 'Library')}</span>
</Link>
{showGlobal && (
  <Link href="/global" className={isActive(location, '/global') ? styles.itemActive : styles.item}
        aria-current={isActive(location, '/global') ? 'page' : undefined} onClick={onNavigate}>
    <Globe size={18} className={styles.icon} aria-hidden="true" focusable={false} />
    <span>{t('Global Library')}</span>
  </Link>
)}
```

### 2.2 Page anatomy (`/global`)

Reuses the catalog's skeleton, not its content scope:

- Header: `H1 Global Library` + the existing count chip (`BrowseList.module.css` `.count`
  pattern — total archive size).
- One persistent sub-line under the header, muted (`--text-muted`, `--fs-sm`):
  **"The whole archive. Add books to your library from here."** — this orients the mode split
  on first contact and never repeats elsewhere.
- Controls row, two pieces, both existing patterns:
  1. **Scope segments** — a two-segment control in the `BrowseList` view-toggle style
     (`role="group"`, `aria-pressed`, 40px min targets): **All** (existing msgid) ·
     **Not in your library**. Persisted per browser via `usePersistentBool`
     (`cwng:global-unowned-v1`), default **All**. Maps to the `scope` query param
     (server-side — see §1).
  2. **Text filter input** — `Catalog`'s search input styling; accepts `?q=` deep links
     (the search page's empty state links here, §8.3).
- Grid: the catalog grid verbatim — `Catalog.module.css` `.grid` + `.density_*` classes, and
  it **shares the same persisted density key** (`cwng:catalog-density-v1`) so both grids look
  identical without a second setting. View-settings popover (sort/density) is reused; the
  Discover rail, random block, BulkBar and selection mode are **not** part of this surface (v1).

### 2.3 What the card reuses — and what it must not

Reuse `BookCard` with a new opt-in prop, in the same spirit as `quickEdit` / `hideActions`:

```tsx
/** Global-library surfaces only. 'unowned' swaps the hover action row for a
 *  persistent Add chip; 'owned' adds the quiet "In your library" cover badge.
 *  Omit on every My-Library surface — there, membership is the whole list and
 *  marking it per card would be noise. */
membership?: 'owned' | 'unowned';
onAddToLibrary?: (book: Book) => void;
addPending?: boolean;
```

Rendering rules:

| Card state | Cover badge row | Action row (below metadata) |
|---|---|---|
| `membership="owned"` | existing badges + **"In your library"** pill (`BookCheck` 12px + label, styled on `.hiddenBadge`'s `--cover-control-*` tokens — quiet, NOT green: green is owned by the Read badge) | today's normal row (`Read now` + pencil), hover-gated as usual |
| `membership="unowned"` | existing badges only | **persistent Add chip** in the `Read now` slot: `BookPlus` + visible label **"Add"**, `aria-label={t('Add {title} to my library')}` |

The Add chip's CSS is `.readNow`'s flex/min-size rules with **one deliberate override:
`opacity: 1` always**. Rationale: on this surface the chip *is the card's meaning*. The
hover-reveal pattern (opacity 0 until hover/focus-within) would force a mouse user to scrub
every card to discover what is addable — the glance test fails on exactly the surface built
for it. Touch already forces controls visible (`@media (any-hover: none)` block,
`BookCard.module.css:390`), so phones get this for free; the override equalizes desktop.

What the surface **must not** inherit from the catalog vocabulary:

1. **No `onRemove` × on global cards.** The cover-top-left × means "remove from this list's
   defining set" (shelves today, My Library root below). On `/global` removal is off-surface
   by design — keeping Remove away from Add at card level is half of the remove/delete
   separation (§5).
2. **`hideActions` (fork #1054) must not suppress the Add chip.** Users who hid card actions
   (they read on e-readers) would otherwise find an inert grid on the one surface whose whole
   purpose is the action. `hideActions` still drops `Read now` on *owned* global cards —
   unchanged semantics — but the Add chip on unowned cards renders regardless. Say so in the
   prop's comment.
3. **No per-user hidden filtering.** Global shows the whole archive; a book the user hid
   carries its existing `Hidden` badge there (so nothing becomes undiscoverable), exactly
   like catalog "show hidden" mode.
4. **No selection mode / BulkBar in v1** (see §11 — deliberate omission).

### 2.4 Glance test, stated

"Which of these are already mine?" — three independent, non-colour signals, because any one
alone is weak:

- Unowned cards carry a **labelled** amber-text chip ("Add") below the metadata; owned cards
  do not.
- Owned cards carry a **labelled** "In your library" pill on the cover; unowned do not.
- The two markers sit in different positions (action row vs cover bottom-left badge row) and
  use different shapes (chip vs pill), so the scan works by layout alone.

---

## 3. The introduction card (operator ruling 1)

A one-shot card that introduces the feature, teaches the §0 separation, and points at the
global library. It appears for a user who has never seen it, is dismissible, and must not
become permanent chrome.

### 3.1 Vehicle: the existing announcement queue — not new chrome

Reuse `components/AnnouncementBanner.tsx` — it was built for exactly this: top-of-shell
announcements with **per-id dismissal that never returns** (localStorage,
`cwng_banner_dismissed:<id>`), `role="status"`, an accessible close button with focus
restoration to `#main`, and a documented "add future top-slot announcements here" queue with
priorities and channels (`announcementQueue.ts`). The card is one new entry, id
`library-intro-v1`, priority above the help/Ko-fi entries for the rollout period:

```tsx
{
  id: 'library-intro-v1',
  priority: 300,                 // above help(200)/kofi(100) while the feature rolls out
  variant: 'help',               // the teal teaching banner — deliberately not accent amber:
                                 // this is information, not an action prompt
  dismissLabel: 'Dismiss library introduction',
  eligible: (me) => !!me?.id && !me.role?.anonymous && !!me.has_own_library,
  content: (t, me) => /* body variant by role — §3.3 */,
}
```

Two small extensions to the component, both noted for the lane:

1. An optional **`eligible?: (me) => boolean`** predicate per entry, evaluated with `useMe()`
  (the banner already renders inside the authenticated `AppShell`, so `me` is available).
  Channel-less entries without a predicate stay eligible as today — zero change to the two
  existing entries.
2. The content function gains `me` as a second parameter so the body can pick its variant.

**Why the announcement queue and not a DiscoverSection-style box:** DiscoverSection is
catalog-only chrome with a book strip and parent-persisted per-browser hiding — the wrong
shape (the intro is a sentence, not a shelf of books) and the wrong scope (a user should meet
it wherever they land, once). The queue already guarantees the three required properties —
never-seen users see it, dismissal is one tap, and a dismissed id can never become permanent
chrome — with zero new infrastructure.

**Retirement is part of the design:** after one release cycle the entry is deleted from the
array (the queue's own rule: never reuse an id). The card is onboarding, not furniture.

### 3.2 Who sees it

`eligible` above: signed-in, non-guest, **selection mode**. Whole-library users are excluded
deliberately — the card's teaching is selection-mode-specific, and in whole-library mode the
mode switch's own copy (§4.2) carries the idea. Everyone starts in selection mode after the
migration (§4.5), so in practice every existing user sees it once before ever being in whole
mode. Guests own nothing, so they see nothing.

### 3.3 The copy (two variants, one card)

Lead span (both variants):

> **New: your own library**

Body, `role.browse_global` present:

> **The library is shared and holds every book once — what you keep is your own selection.
> Nothing you had is gone. Every book, new arrivals included, is under Global Library in the
> menu.**

Body, no `browse_global`:

> **The library is shared and holds every book once — what you keep is your own selection.
> Nothing you had is gone. Your administrator manages what enters your selection.**

How each clause earns its place: the lead names the feature in three words; clause one is the
operator's required idea (account ≠ library; library shared; what you keep is yours); clause
two pre-empts the only panic this feature can cause ("my books are gone" — they are not, and
the migration guarantees it); clause three is the required pointer, worded "in the menu" so
it is true on desktop (sidebar) and phone (drawer) alike. The no-role variant swaps the
pointer for the honest door (the administrator), matching every other no-role surface in
this design.

**Dismissal scope — per browser, deliberately.** The queue's localStorage mechanism means a
user meets the card once per browser (phone + desktop = twice). That is the established
behaviour of every announcement in this product, and for a *navigation* teaching card a
per-device showing is defensible: the card says where things are on the device you're
holding. If once-per-user-ever is wanted instead, the upgrade is one field
(`me.library_intro_dismissed` + a dismiss endpoint) — flagged in §14, not built in v1.

Classic equivalent: §10.

---

## 4. The two named modes and their switches (operator ruling 2)

"Monolibrary" is the internal word. It never appears in the interface. The reader-facing
names are **The whole library** and **My selection** — chosen because they name what the
reader *sees*, not the architecture; they form a minimal pair (whole vs chosen); both are
short enough for a phone-width radio label; and both translate cleanly
(fr *« Bibliothèque entière » / « Ma sélection »*, nl *"Hele bibliotheek" / "Mijn selectie"*).
Rejected: "Monolibrary" (jargon, untranslatable, reads as a bug report); "Complete library"
("complete" invites a storage/quality misreading); "Everything" / "Just mine" (too vague to
anchor a confirm dialog).

### 4.1 The user's own control — Account page

A new block on `pages/Account.tsx`, placed beside the e-reader/sync preferences (the mode
decides *what* syncs — the two settings are causally linked, so they should be read
together). A real `<fieldset>`/`<legend>` + radio pair — the exact pattern of the catalog's
density picker (`Catalog.tsx:880-885`), so keyboard and screen-reader behaviour come free:

```
Library contents
( ) The whole library — Everything on the server, including every new book added to it.
(•) My selection     — Only the books you choose. Add them from the global library;
                       remove them any time.
```

The radio shows current state (not a hidden flag), so the control is also the *explanation*
of which mode you're in — there is no separate status line to keep in sync.

**Gating:** the block renders only for a signed-in, non-guest user **with
`role.browse_global`**. Without the role, switching yourself to the whole library would
self-grant exactly the visibility the role gates (a hand-curated account could escape its
curation), so no-role users see one static muted line instead of the control:

> **Your library contents are managed by an administrator.**

This makes `browse_global` the de-facto "may choose their own mode" role — called out in the
pushback notes (§14) so the coupling is a decision, not an accident.

### 4.2 The switch copy, both directions (user)

A mode flip is not destructive (rows are kept — §1), but it changes what the e-reader does,
and the device is always the surprise vector. So both directions confirm (`window.confirm`,
the established pattern) and every confirm carries its e-reader sentence. Three variants —
the "to selection" direction splits because a first-ever switch *creates* the selection and
a returning switch *restores* it, and the copy must say which:

→ **The whole library** ("show me everything again"):

> **Show the whole library again? Your selection is kept exactly as you left it — switch back
> any time and it is still there. At its next update, your e-reader syncs the whole library.**

→ **My selection**, restore (`library_book_count > 0` rows exist):

> **Keep your own selection again? Your library goes back to the {count} books you had chosen
> — nothing was lost while you saw everything. At its next update, your e-reader returns to
> your selection.**

→ **My selection**, first time ever (no rows; seed runs):

> **Start your own selection? It begins as everything you can see now, so nothing changes
> until you remove books yourself. Your e-reader keeps the same books at its next update.**

The restore promise ("nothing was lost") is only shippable because the backend guarantees
keep-dormant (§1). The client knows which variant to show from `me.library_book_count` /
the switch response's `seeded` flag (§1).

Success announcements (polite, `useAnnouncer`): **"You now see the whole library."** /
**"Your library now shows your selection."**

### 4.3 The admin's control — and how it relates to the user's

Same flag, same two named modes, two editors. The admin's version lives in the per-user
editor (`pages/Admin.tsx`), replacing any checkbox-shaped treatment: the mode is **not a
role**, and must not render as one more row in the `ROLE_FIELDS` grid (`Admin.tsx:52-61`).
It renders as a labelled radio block below the roles grid, third-person labels:

- Label: **Library contents** (shared msgid with the user side)
- Options: **The whole library** (shared) / **Own selection** (third-person counterpart of
  "My selection" — "My" would be wrong on another user's row)
- Permanent hint beneath (`Admin.module.css` `.settingsHint`):

  > **Switching a user to their own selection first fills it with everything they can see
  > now, so nothing changes for them until they remove books themselves. Switching back
  > keeps the selection intact but unused.**

  That is the "one sentence, no issue-reading" requirement: it names the seed, the order
  (fill *first*), and the no-data-loss guarantee in both directions.

Confirms, mirroring the user's two directions:

→ selection, first time: **"Give {name} their own selection? It starts as a copy of
everything they can see now — nothing changes for them yet."**

→ selection, restore: **"Switch {name} back to their own selection? The {count} books they
had chosen are restored."**

→ whole: **"Show {name} the whole library again? Their selection is kept but no longer used,
and their e-reader syncs the whole library at the next update."**

Success banners (`styles.msgOk`): **"{name} now keeps their own selection ({count} books)."**
/ **"{name} sees the whole library again."** — the count is the admin's proof the seed ran;
it comes from the switch response (§1).

**Relationship:** last write wins, no lockout. If an admin flips a user to the whole library,
the user's own Account block shows it selected and (role permitting) they can flip back; the
admin sees the user's own choice on their next visit. Neither control hides the other's
effect — both edit the one flag, and both UIs read it fresh from the server.

**Guest/anonymous** accounts never show the block (same exclusion as the classic role
cluster).

### 4.4 What the rest of the UI does in whole-library mode

Everything the mode name promises, and nothing else:

- **No Global Library section.** Your library already *is* the global library; a second
  section would be the same grid at two URLs, forking the vocabulary. Sidebar entry and
  `/global` route both disappear (deep links redirect to `/`).
- **No add/remove affordances anywhere — hidden, not disabled-with-reason.** Decided and
  justified: disabled-with-reason is the right pattern when a capability is unavailable *to
  this user* but might be wanted (a door they could ask for). Here the user holds the switch
  themselves, one screen away in Account; a greyed "Add to my library" chip on every card
  would advertise a distinction the user explicitly switched off, and each instance would
  need its own `aria-describedby` explanation chrome. The affordance's absence *is* the
  mode's meaning.
- Concretely: the card × never renders, the detail-page membership chip is absent, search
  and OPDS are unscoped, the sidebar root label is **Library**, and `hideActions` etc. behave
  exactly as they do today. The Account block is the **only** surface where the selection
  concept exists in this mode.
- The selection's data is untouched and dormant (§1) — shelves, read state, highlights and
  progress were never tied to the mode flag anyway.

### 4.5 The migration (operator ruling 3) — what day one looks like

Every existing account wakes in **My selection** mode with its selection seeded to exactly
what it could see the day before. Day one is byte-identical: same catalog, same OPDS feed,
same e-reader sync set (the seed *is* the spec's seed-on-enable, run for everyone at once —
per-user × N accounts, chunked, lane's concern). The visible deltas on day one are exactly
three, all intentional: the introduction card (§3), the sidebar label becoming **My
Library**, and the **Global Library** entry appearing for role holders.

From day two, newly ingested books land in the global library only — that is the feature
working, and the card's copy already says so ("new arrivals included, under Global Library").
One rule closes the last surprise hole: **uploading a book adds it to the uploader's own
selection automatically.** An upload that landed only in the archive would read as "my
upload vanished" in selection mode. (Admin ingest/scan adds to *nobody's* selection — books
arrive in the Global Library, which is what the card teaches.)

New registrations start in **the whole library** mode — today's behaviour, zero surprise for
a brand-new account — and their first switch to My selection is the seeding one (§4.2 third
variant).

### 4.6 The spectrum test (operator ruling: one sentence per mode)

> **My selection:** your library holds only the books you picked.
> **The whole library:** your library is everything on the server, new books included.

Audit rule applied to every string in this document: a reader must never need to know a flag
exists. Pass conditions — no string contains "monolibrary", "membership", "entitlement",
"flag", "enable", or "disable"; every mode reference is one of the two names; every confirm
states its consequence in terms of what the person *sees* (library view, e-reader), never in
terms of what the database does. The §12 inventory is written to pass this test; reviewers
should reject any new string that doesn't.

---

## 5. The two destructive-looking actions

The pair that must never be confused:

| Dimension | **Remove from my library** | **Delete from the global library** |
|---|---|---|
| What it does | drops the book from your selection; file, metadata, highlights, progress all kept | erases file + record for everyone |
| Reversible | yes (re-add) | no |
| Role | any signed-in non-guest in selection mode | `role.delete_books` (+ edit, server-enforced) |
| Placement | ordinary actions row / card × (My-Library root only) | separated `.dangerZone` below a `border-top`, own heading |
| Icon | `BookMinus` (detail) / `X` (card, existing removeBtn pattern) | `Trash2` |
| Style | ghost chip (`readToggleGhost`: border, neutral text) | `.actionDanger` (danger border + text) |
| Confirm verb | "Remove … from your library" | "Delete … from the global library" |
| After success | stay on page, card/page flips to unowned | navigate to `/` (today's behaviour) |

Colour is one signal among seven (placement, heading, icon, verb, style, confirm text,
outcome). A colour-blind user, a screen-reader user, and a hurried mobile user each get at
least three of the others.

### 5.1 Remove from my library — SPA

**Entry points (SPA):**

- **Book detail, owned** (`BookDetail.tsx` actions row, `:416`): a chip placed immediately
  after the read toggle, before `AddToShelf` — inside the personal cluster, far from the
  danger zone below the fold of the row. Ghost style, `BookCheck` icon, label **"In your
  library"**, `aria-label={t('Remove from my library')}` (the label names the action the
  click performs — the established read-toggle pattern, see the classic comment at
  `detail.html:683-690`). Ghost, **not** `readToggleActive` amber: owned is the majority
  state, and an amber chip on every book page devalues the accent. Click → confirm (§5.3) →
  mutate; pending state `disabled` + label **"Removing…"**.
- **Catalog root `/` in selection mode**: cards get the existing `onRemove`/`removeBtn`
  mechanism (`BookCard.tsx:190-199`) with `removeLabel={t('Remove {title} from my library')}`
  — the exact precedent is `Shelf.tsx:333-334`. Click → **confirm dialog** (§5.3) →
  optimistic drop (§9.4). The × appears **only** on `/` and on shelves — never on `/global`,
  never on browse facets (`/authors/5`…), never on search results. Rule: *the card × means
  "remove from this list's defining set" and appears only where that set is yours to manage.*
  The glyph collision with shelf-remove is deliberate and safe: same interaction family
  (membership in a scoped list), and the mandatory confirm — which shelf-remove lacks —
  carries the severity difference.

**Never offered:** to anonymous sessions (same gate as Hide, `BookDetail.tsx:553`), in
whole-library mode (§4.4), and on books the user doesn't own (they see Add instead).

### 5.2 Delete from the global library — SPA

Stays exactly where it is (`BookDetail.tsx:575-604`), two copy changes:

- Section heading **"Delete book" → "Delete from the global library"** (new msgid; the
  heading is the orientation layer, the button keeps the existing `Delete` msgid).
- Confirm string replaced (the old one says "your library" — false under membership):

> **Delete "{title}" from the global library? The book and all its files are permanently
> erased for every member. This cannot be undone.**

### 5.3 The actual confirm copy (remove)

`window.confirm`, matching the established pattern (`reloadMetadata` at `BookDetail.tsx:513`,
`deleteBook` at `:588`). Assembled from sentence msgids so the conditional parts don't
multiply full-string variants. **Order is library first, device second, shelves third**
(operator ruling: the primary noun is the user's library; shelves are secondary curation
inside it — the confirm leads with what leaves the library, then the consequence the user
will actually observe, then the shelf detail):

```
Remove "{title}" from your library?

The next time your e-reader updates, this book disappears from it.
It also leaves these shelves: {shelves}.                          ← only when on shelves
Nothing is deleted: the book stays in the global library, and your
highlights, notes and reading progress are kept.
You can add it back any time from the global library.             ← if role.browse_global
Only an administrator can add it back.                            ← if not
```

Why each sentence earns its place:

1. **Names the action and scope** — "your library", not "the library".
2. **The e-reader consequence in device language.** No "sync", no "entitlement": the book
   *disappears from the device* at its next *update* — the event the user will actually
   observe. Unconditional on owning an e-reader: for a web-only user the sentence is inert,
   and a conditional variant would double the string count.
3. **Shelves**, comma-joined from the existing `useBookShelves` membership — the first
   brief's requirement, kept, demoted to third position. The spec's open question ("does
   removing also clear shelf memberships, or block?") is answered here in the UI: removal
   *clears* them, and the user learns it by name before committing; blocking would strand
   the common case.
4. **The reassurance that makes the action feel safe** — and it is true by design: per-user
   data is keyed `(user_id, book_id)` and survives the book leaving the library (spec).
5. **The recovery path, truthful per role.** A user without `browse_global` cannot re-add
   themselves; telling them so is what keeps this confirm honest for them.

### 5.4 Classic theme versions

Classic `detail.html` action bar (`.book-action-bar`, `:640-830`):

- **Membership toggle**: one `btn btn-default action-icon-btn`, placed directly after the
  archive toggle (the last personal-state button before the editor group). Glyph pair
  `glyphicon-plus` / `glyphicon-ok` (the matched-pair swap pattern used by read/favorite/
  archive), `title`/`aria-label` **"Add to my library"** / **"In your library"**,
  `data-toast-on/off` **"Added to your library"** / **"Removed from your library"**. Add
  fires immediately; **remove opens a modal, not a toast**.
- **Remove modal**: new `remove_from_library` macro in `modal_dialogs.html`, cloned from
  `delete_book` (:40-69) with two changes: header class `bg-info` (not `bg-danger`) and the
  body carries the §5.3 sentences (same msgids — shared catalogs translate both themes).
  Footer buttons: `btn-default` **"Remove"** + Cancel. The neutral header is the classic
  counterpart of the SPA's ghost-vs-danger styling: red is reserved for the real delete.
- **Delete**: unchanged position (`btn-danger`, `glyphicon-trash`, `#deleteModal`); the macro
  body gains one line so classic matches the new truth: **"It disappears from every member's
  library and e-reader."**
- **Classic grid cards get no new per-card control.** The classic grid (`index.html`) has no
  per-card action vocabulary today; adding one would be new chrome, not parity. On the
  classic global page, owned cards get the **"In your library"** badge through the existing
  `image.cover_badges` macro, and unowned cards get a persistent text link **"Add to my
  library"** under the author line. Removal happens from the detail page — it is simply the
  only affordance present.

---

## 6. The empty state (My Library, zero books)

`components/EmptyState.tsx` is extended, backwards-compatibly (every existing call site
passes only `message`):

```tsx
interface EmptyStateProps {
  message: string;
  /** Optional heading above the message — the empty library needs a headline,
   *  not just a sentence, because "blank grid" reads as "the server lost my books". */
  title?: string;
  icon?: LucideIcon;          // default Library
  children?: ReactNode;       // optional CTA row under the message
}
```

Rendering on `/` in selection mode when `items.length === 0`:

- Icon: default `Library`, 40px.
- Title: **"Your library is empty"**
- Message (with `role.browse_global`):

  > **Nothing is missing — the whole library is still on the server. What you see here is
  > your own selection. Add books from the global library; they appear here and on your
  > e-reader.**

  The first three words do the work: the user's fear on seeing a blank grid is data loss, so
  the copy opens by denying it, *then* explains the model, *then* points at the door.
  (Revision 2 dropped "on your shelves" from this sentence — the empty library sells the
  library and the device, not secondary curation.)

- CTA (children): a primary-styled link (`BookDetail.module.css` `.actionPrimary` look) to
  `/global`: **"Browse the global library"**.

- Message (without `role.browse_global` — no CTA):

  > **Your administrator chooses which books are in your library. Ask them to add books, or
  > to let you browse the global library.**

Untouched: the whole-library-mode empty catalog ("No books here." — today's copy), and the
`/global` empty-archive case, which reuses the generic copy. One extra empty case is new and
worth its string: the **"Not in your library"** segment with nothing left to add →
**"Every book here is already in your library."** — the one place an empty grid is *good
news*, and the copy says so.

---

## 7. Add-to-library entry points

### 7.1 Book card (Global grid)

The persistent Add chip from §2.3. Behaviour: click → chip goes `disabled`, keeps its label,
shows the 13px `Spinner` beside the icon (`Spinner` component exists; sizing precedent
`AddToShelf.tsx:238`), and on success the card re-renders as owned. Failure: flip back and
announce (§9.4). The chip is a `<button>`, a **sibling** of the card link — the card's
hard invariant (BookCard comment, `:166-167`) is that actions never nest inside the `<a>`.

### 7.2 Book detail

- **Unowned book** (reached from `/global` or a deep link): **"Add to my library"** takes the
  `actionPrimary` slot (first position, amber, `BookPlus` icon). Per the §0 invariant,
  `Read now` / format downloads / send-to-e-reader are **hidden** while unowned; so are the
  personal-state chips (Mark as read, Favorite, Archive, Hide) — per-user state on a book you
  don't have is noise. What remains alongside Add: `AddToShelf` (itself an add path — §7.4),
  the editor group (Edit/Reload/cover — editing is a global act by design, spec), and the
  danger zone for delete-role users. ⚠️ Flag for the lane: if the backend instead *allows*
  read/download for non-members, render those buttons ghosted below Add rather than hidden —
  the UI must mirror whatever the API enforces, and the spec leaves that enforcement to the
  `helper.py` per-entry-point audit.
- **Owned book**: the row renders exactly as today, plus the "In your library" chip (§5.1)
  after the read toggle. On removal completing, the page flips in place to the unowned
  rendering — no navigation, the announcer confirms.

### 7.3 Search results

Search stays scoped to My Library (it rides `common_filters`, so it is scoped for free — and
that is correct: search is a "find in my stuff" verb). The unowned-book encounter happens at
**zero results**: when in selection mode with `role.browse_global`, the empty-results block
gains one line + link under the existing "no matches" message:

> **Search the global library for "{query}" instead**

→ `/global?q={query}` (the global filter input accepts the deep link). This covers the "user
meets a book they don't own via search" moment without rebuilding search, and without
teaching scopes as a UI concept. The full archive-wide join in the main search box is the
spec's explicit *Later* item and stays out of v1.

### 7.4 Shelf-implied add (no second dialog)

A shelf cannot hold a book outside your set (spec: `shelf.py` becomes membership-aware), so
adding an unowned book to a shelf must add it to the library first — disclosed inline, never
as a modal. (This is a shelf surface leading with the shelf, which is correct — ruling 4
demotes shelves from *library* copy, not from shelf flows.)

- In `AddToShelf.tsx`, when the book is unowned, the open panel shows a one-line hint pinned
  at the top, styled on `.empty` (`AddToShelf.module.css:108`), above the shelf list:

  > **Adding this book to a shelf also adds it to your library.**

- Toggling a shelf then performs membership-add → shelf-add as one gesture. On success,
  announce **"Added to your library and to {shelf}"** (the implicit half is said out loud —
  that is the disclosure, not a dialog). If membership fails, the shelf call is never made
  and the failure announcement names the add. If membership succeeds but the shelf call
  fails, the book stays in the library (the hint already disclosed that ordering).
- Classic: the same sentence appears as a non-actionable header `<li>` at the top of the
  `add-to-shelves` dropdown (`detail.html:783-799`) on an unowned book; the server performs
  membership-add first.

### 7.5 Upload (implicit, always)

Uploading a book adds it to the uploader's own selection automatically (§4.5) — no dialog,
no chip; the upload success flow already lands the user on the book in their library. This
entry point exists so the lane doesn't ship the "my upload vanished" bug as a feature.

---

## 8. Admin surface

The admin's mode control is designed in §4.3 (it is the same two named modes, not an
"enable" checkbox). What remains here:

1. **Role toggle** — add to `ROLE_FIELDS` (`Admin.tsx:52-61`):
   `{ key: 'browse_global', label: 'Browse global library' }`. Behaves like every other role
   toggle (`toggleRole`, `:84`).
2. **The no-role warning** — selection mode without `browse_global` is **allowed** (a hard
   refusal would break legitimate hand-curated accounts; spec open question answered in UI),
   with an inline warning under the mode block whenever the user is in selection mode and
   lacks the role:

   > **Without the global-browse role, only an administrator can add books to this user's
   > library.**

3. **Seeded-count feedback** — the §4.3 success banner carries `{count}` from the switch
   response; it is the admin's proof the seed ran.

### 8.1 Classic (`cps/templates/user_edit.html`)

- `browse_global_role` checkbox in the existing role cluster (:446-487), label **"Browse
  global library"**, same gating (`role_admin()`, not anonymous).
- The mode control as a two-radio block **outside** that cluster, directly beneath it, with
  the §4.3 hint as a permanent `<p class="help-block">`; gated
  `current_user.role_admin() and not new_user and not content.role_anonymous()`. The same
  template's **profile** branch (`profile` conditionals) renders the §4.1 first-person
  version for the signed-in user — classic has no separate account page, so the user_edit
  profile view is where the user's own switch lives. Classic has no per-field confirm
  pattern, so the persistent hint carries the teaching the SPA's confirms carry — acceptable
  asymmetry, matching how classic already treats `kobo_only_shelves_sync` (consequence-
  bearing, hint-only).

---

## 9. States

### 9.1 Loading

- Pages (`/`, `/global`): `SpinnerCentered` (existing). Catalog's first-load grid spinner
  pattern (`Catalog.tsx:929-934`) is reused as-is on `/global`.
- The membership chip on detail and the Add chip on cards show their **pending** form
  (disabled + spinner + "Adding…"/"Removing…"), never a bare freeze.
- The mode-switch block: radios `disabled` with the pending selection shown while the switch
  (and any seed) runs — a mode control that appears to ignore its click is worse than a
  blocked one.
- The introduction card has no loading state: it renders from local `me` data, and if `me`
  hasn't resolved the banner simply doesn't render yet (the queue already behaves this way).

### 9.2 Empty

Covered in §6 (my library, both roles), §2/§6 (global empty archive), §6 (unowned-segment
empty). Search empty: §7.3.

### 9.3 Permission-denied

- `/global` deep link without `role.browse_global`: the API 403s; the page renders
  `EmptyState` with **"You don't have access to the global library. Ask an administrator to
  grant it."** — not a bare error dump, and not a redirect that hides *why*.
- `/global` in whole-library mode: client-side redirect to `/`. Their library *is* the global
  library; maintaining a second identical view would only fork the vocabulary.
- Selection mode without the role: every surface works, minus the global entry points —
  sidebar entry absent, empty-state CTA absent, remove-confirm uses the "Only an
  administrator" sentence, Account shows the static managed line (§4.1). Nothing renders as
  broken; the copy always names the administrator as the door.

### 9.4 Optimistic add/remove (the 300 ms window)

Two precedents already in the tree set the pattern — `Shelf.tsx:136-140` (optimistic local
drop, invalidation reconciles) and `queries.ts:881-889` (`onMutate` snapshot + rollback
context). Apply them to one boolean:

- **Add (global card / detail)**: on mutate, flip `in_library` in the list/detail cache via
  `setQueryData` immediately — the card re-renders owned within a frame; the pending spinner
  still shows on the triggering control so the gesture has a visible in-flight state. On
  error, roll back the snapshot and announce **"Could not add the book. Please try again."**
  (assertive). On settled, invalidate the book lists (same keys catalog invalidation uses).
- **Remove (catalog × / detail chip)**: **confirm first, then optimistic** — the confirm is
  mandatory (§5.3), so the optimistic phase starts after it. Optimistically drop the card
  from the `/` list; on failure re-insert + announce **"Could not remove the book. Please try
  again."**
- **Mode switch**: NOT optimistic. The switch may trigger a chunked seed (first time) or a
  restore — the radio blocks (disabled, current selection shown) until the response arrives
  and `me` is invalidated; the success announcement follows the response, never the click. A
  wrong optimistic mode flip would flash the wrong library at the user.
- **Focus**: a removed card unmounts; focus falls to the document — same behaviour as the
  shelf-remove precedent, with the announcer carrying the outcome. Do not invent new focus
  choreography here; parity first, and any improvement lands for both surfaces together.
- **Success announcements** (polite): **"Added to your library"** / **"Removed from your
  library"**.
- The one hard rule for the window: the triggering control is `disabled` while in flight
  (`isPending`), so a double-tap can never fire add→remove or remove→remove against a
  half-settled state.

---

## 10. Classic theme — surface-by-surface summary

| SPA surface | Classic equivalent |
|---|---|
| Sidebar "My Library" / "Global Library" | Navbar primary links in `layout.html` (implementer locates the exact list there): library link label switches with the mode; "Global Library" link added beside it, selection mode + role only |
| `/global` page (grid, segments, filter) | `index.html` rendered with a `page='global'` variant: same grid + sort controls; the All/Not-in-your-library segment becomes two `btn btn-primary` filterheader buttons (`list.html:14-17` pattern); owned cards get the badge via `image.cover_badges`, unowned cards a text **"Add to my library"** link under the author line |
| **Introduction card (§3)** | Bootstrap `alert alert-info alert-dismissible` strip at the top of the classic index page, selection mode + signed-in only, same msgids. Dismissal per browser in localStorage — **using the same `cwng_banner_dismissed:library-intro-v1` key** so a browser that dismissed it in one theme never sees it in the other |
| **Mode switch, user (§4.1)** | `user_edit.html` profile branch: the same two-radio block with descriptions, gated on the role; no-role users get the static managed line (§8.1) |
| **Mode switch, admin (§4.3)** | `user_edit.html` admin branch: third-person radio block + permanent help-block hint (§8.1) |
| Detail membership chip + remove confirm | Action-bar toggle button (§5.4) + `remove_from_library` modal macro |
| Detail danger zone + new delete copy | Existing `#deleteModal` + the added every-member sentence (§5.4) |
| Empty my library (title, copy, CTA) | `index.html` empty block: in selection mode, the empty-grid message is replaced by the §6 copy with a plain link to the global page |
| Admin role toggle | `user_edit.html` `browse_global_role` checkbox (§8.1) |
| Search empty → global link | `search.html` no-results block: same sentence + link when the role allows |
| AddToShelf disclosure | Header line in the `add-to-shelves` dropdown (§7.4) |

Classic relies on the same msgids throughout (shared `.po` source), so the classic column
adds **zero** new strings beyond the two classic-only lines flagged in §12.

---

## 11. Deliberately out of scope (v1)

- **Bulk remove / bulk add** (BulkBar + selection mode on `/` or `/global`). The remove
  confirm must *name shelves*; a bulk confirm cannot do that honestly for N books. Per-book
  removal only, v1.
- **Archive-wide search joined into the main search box** — spec's own *Later* item; §7.3's
  deep link is the v1 bridge.
- **Rule-based auto-add and a per-user "hide the global library entirely" setting** — spec
  *Later* items; no UI surface reserved beyond the sidebar entry being pinned-but-independent.
- **`added_by` and any "someone added this for you" notice — dropped from v1 by operator
  ruling** (not deferred: dropped). No surface in this document reads the column.
- **OPDS / Kobo client UI** — none exists to design; scoping is server-side, which is the
  point of the feature. No OPDS "global" catalog in v1.
- **Per-card remove in the classic grid** — no per-card control vocabulary exists there to
  extend (§5.4).
- **Once-per-user (server-side) intro-card dismissal** — v1 ships the queue's per-browser
  dismissal; §14 flags the upgrade path.

---

## 12. String inventory (i18n contract)

Every new user-visible string. English source = msgid key; the lane must land fr/nl catalog
entries for all of them (catalogs gated at 100%). Total: **54 new + 2 replaced** for the
whole feature, both themes included. Revision 2 added the introduction card and the mode
switch (+22 strings over revision 1) and retired 5 revision-1 strings before they ever
reached a catalog (the admin "Personal library" enable/disable set — superseded by the mode
framing), so nothing retired costs translator churn. Held down by: assembling confirms from
sentences, sharing msgids across user/admin and SPA/classic verbatim, and reusing existing
vocabulary wherever it already says the right thing (list at the end).

**Mode names & the spectrum (§4)** — *new in revision 2*
1. `Library contents`
2. `The whole library`
3. `Everything on the server, including every new book added to it.`
4. `My selection`
5. `Only the books you choose. Add them from the global library; remove them any time.`
6. `Your library contents are managed by an administrator.`

**Mode-switch confirms & announcements, user (§4.2)** — *new in revision 2*
7. `Show the whole library again? Your selection is kept exactly as you left it — switch back any time and it is still there. At its next update, your e-reader syncs the whole library.`
8. `Keep your own selection again? Your library goes back to the {count} books you had chosen — nothing was lost while you saw everything. At its next update, your e-reader returns to your selection.`
9. `Start your own selection? It begins as everything you can see now, so nothing changes until you remove books yourself. Your e-reader keeps the same books at its next update.`
10. `You now see the whole library.`
11. `Your library now shows your selection.`

**Mode-switch, admin (§4.3)** — *new in revision 2 (replacing revision 1's retired admin set)*
12. `Own selection`
13. `Switching a user to their own selection first fills it with everything they can see now, so nothing changes for them until they remove books themselves. Switching back keeps the selection intact but unused.`
14. `Give {name} their own selection? It starts as a copy of everything they can see now — nothing changes for them yet.`
15. `Switch {name} back to their own selection? The {count} books they had chosen are restored.`
16. `Show {name} the whole library again? Their selection is kept but no longer used, and their e-reader syncs the whole library at the next update.`
17. `{name} now keeps their own selection ({count} books).`
18. `{name} sees the whole library again.`

**Introduction card (§3.3)** — *new in revision 2*
19. `New: your own library`
20. `The library is shared and holds every book once — what you keep is your own selection. Nothing you had is gone. Every book, new arrivals included, is under Global Library in the menu.`
21. `The library is shared and holds every book once — what you keep is your own selection. Nothing you had is gone. Your administrator manages what enters your selection.`
22. `Dismiss library introduction`

**Navigation & pages**
23. `My Library`
24. `Global Library`
25. `The whole archive. Add books to your library from here.`
26. `Not in your library`

**Card & detail actions**
27. `In your library`
28. `Add to my library`
29. `Add {title} to my library` (aria-label)
30. `Remove from my library` (aria-label, detail chip)
31. `Remove {title} from my library` (aria-label, card ×)
32. `Adding…`
33. `Removing…`

**Remove confirm (assembled sentences, §5.3)**
34. `Remove "{title}" from your library?`
35. `The next time your e-reader updates, this book disappears from it.`
36. `It also leaves these shelves: {shelves}.`
37. `Nothing is deleted: the book stays in the global library, and your highlights, notes and reading progress are kept.`
38. `You can add it back any time from the global library.`
39. `Only an administrator can add it back.`

**Announcements & errors**
40. `Added to your library`
41. `Removed from your library`
42. `Added to your library and to {shelf}`
43. `Could not add the book. Please try again.`
44. `Could not remove the book. Please try again.`

**Empty & denied states**
45. `Your library is empty`
46. `Nothing is missing — the whole library is still on the server. What you see here is your own selection. Add books from the global library; they appear here and on your e-reader.`
47. `Your administrator chooses which books are in your library. Ask them to add books, or to let you browse the global library.`
48. `Browse the global library`
49. `Every book here is already in your library.`
50. `Search the global library for "{query}" instead`
51. `You don't have access to the global library. Ask an administrator to grant it.`

**Shelf-implied add**
52. `Adding this book to a shelf also adds it to your library.`

**Admin (unchanged from revision 1)**
53. `Browse global library` (role label)
54. `Without the global-browse role, only an administrator can add books to this user's library.`

**Replaced existing msgids** (both need fr/nl re-translation; the old text becomes false
under membership):
- SPA delete confirm → `Delete "{title}" from the global library? The book and all its files are permanently erased for every member. This cannot be undone.`
- SPA danger-zone heading → `Delete from the global library`

**Retired before shipping** (revision-1 strings that never reach a catalog — no translator
cost): `Personal library`, the revision-1 enable hint/confirm/success, and the revision-1
disable confirm — all superseded by the §4 mode framing.

**Classic-only additions** (server `_('…')`, same catalogs):
- `It disappears from every member's library and e-reader.` (delete-modal body line)
- *(All other classic strings reuse the msgids above verbatim.)*

**Reused for free** (already translated; use, don't paraphrase): `Library`, `All`, `Cancel`,
`Delete`, `Read now`, `Add to shelf`, `Manage shelves`, `Failed to load books.`,
`No books here.`

---

## 13. Accessibility checklist (design-level)

- **Remove vs Delete never rests on colour**: seven independent signals (§5 table), of which
  colour is one. Icon (`BookMinus`/`X` vs `Trash2`), label verb (Remove vs Delete), placement
  (actions row vs bordered danger zone with heading), and confirm scope all differ.
- **Badges follow the established pattern**: `role="img"` + `aria-label` on the "In your
  library" pill (`BookCard.tsx:85-88` precedent), visible label always present — no
  icon-only states.
- **All card controls are siblings of the card link**, never nested in the `<a>`
  (`BookCard.tsx:166-167` invariant) — the Add chip included.
- **aria-labels carry the full action**: `Add {title} to my library`,
  `Remove {title} from my library` — a screen-reader user hears scope, not just a verb.
- **Touch targets**: the Add chip inherits `.readNow`'s 24px floor / 44px touch rules
  (`BookCard.module.css:341-404`); the segments reuse the 40px view-toggle buttons.
- **The mode switch is a real `<fieldset>`/`<legend>` + radio pair** (the catalog density
  picker's pattern, `Catalog.tsx:880-885`) — arrow-key navigation, a programmatic group name,
  and current-state announcement all come from native semantics; nothing custom. Its state is
  never conveyed by colour (checked radio + description text).
- **The introduction card reuses the announcement queue's accessibility wholesale**:
  `role="status"`, a labelled close button, and focus restoration to `#main` on dismiss when
  no further announcement is queued (`AnnouncementBanner.tsx`). Its dismissal is a real
  button, never a timeout — the card stays until the user dismisses it.
- **State changes are announced** (`useAnnouncer`): add/remove success polite, failures
  assertive, mode switches announced on server confirmation (§9.4) — never on click.
- **Dialogs**: `window.confirm` is the codebase's established confirm and is screen-reader
  serviceable; the classic remove modal clones the `delete_book` modal semantics
  (`role="dialog"`, `aria-labelledby`).
- **Contrast**: the Add chip uses `--accent-text` on the page surface (AA-verified tokens);
  the owned pill uses the self-contrasting `--cover-control-*` pair because it sits on
  uncontrolled cover artwork — same rule the Hidden badge follows
  (`BookCard.module.css:202-219`). No new colour pairs are introduced anywhere in this
  design; that is deliberate, so the AA table in `tokens.css` needs no new rows.

---

## 14. Open items handed to the implementing lane (and two flagged for the operator)

1. **Backend read/download enforcement for non-member books** decides §7.2's hidden-vs-ghosted
   rendering. The UI must not hard-code either; read it from what the detail payload offers.
2. **Shelf-clear-on-remove** — this design answers the spec's open question with "removal
   clears shelf memberships, disclosed by name in the confirm" (§5.3). If the lane picks
   block-instead, string 36 and the confirm flow change shape; flag back before building
   that variant.
3. **`library_book_count` / `seeded` in the switch response** (§1) power the {count} copy
   and the first-time-vs-restore split. If counts can't be returned cheaply, drop {count}
   from strings 8/15/17 rather than dropping the sentences — never fake the number.
4. ⚠️ **Operator flag — the migration reading.** This design reads ruling 3 literally: every
   existing account wakes in **My selection** mode, seeded to its visible set (§4.5). The
   cost to know about: from day two, books *other people* add stop appearing automatically
   for existing users — that is the feature, and the card teaches it, but it is still a
   behaviour change for anyone who never reads the card. The alternative reading (everyone
   stays in whole-library mode until they opt in) makes day-N identical too, at the price of
   the feature being opt-in-forever for the existing base. The copy supports either; the
   seed job and the §4.2 confirm variants are written for the literal reading.
5. ⚠️ **Operator flag — `browse_global` is the de-facto mode-switch key** (§4.1). A user
   without the role cannot flip their own mode, because self-flipping to the whole library
   would self-grant the visibility the role exists to gate. If you want mode choice and
   global browsing to be separately grantable, that needs a second role bit — say so before
   the lane hard-codes the coupling.
6. **Intro-card dismissal scope** (§3.3): v1 ships per-browser (the queue's mechanism). The
   once-per-user upgrade (`me.library_intro_dismissed` + endpoint) is one field if repeat
   showings across a user's devices draw complaints.
