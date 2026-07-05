# "What's New" — in-app feature log (SPA) + per-release skill

**Status:** BUILT (design captured 2026-06-28; implemented 2026-07-04). Operator-requested.
**Owner surface:** new React UI (`/app`) only. No classic-UI counterpart needed.

**As-built notes (deltas from the original design):**
- Page: `frontend/src/pages/WhatsNew.tsx` + `.module.css` — vertical amber-rail
  timeline, native `<details>` collapsibles (newest open), category-tinted chips,
  "Try it" deep-link buttons, English-entries footnote, empty-state guard.
- Data: `frontend/src/data/whatsNew.ts` (typed `WHATS_NEW`, seeded v4.1.5→v4.1.0).
- Unread dot: `frontend/src/lib/whatsNew.ts` — keyed to `LATEST_WHATS_NEW_VERSION`
  (the newest version baked into the data file), NOT a runtime `INSTALLED_VERSION`
  (no such SPA constant exists). `markWhatsNewSeen()` persists to localStorage
  (`cwng_whats_new_seen_version`) + fires a same-tab window event so the dot clears
  live. Dot lights whenever seen ≠ latest (incl. first-ever visit — one discovery
  nudge, cleared on open).
- Help menu + route wired in `TopBar.tsx` / `App.tsx`; chrome anchored in
  `cps/spa_strings.py`.
- Per-release skill: `~/.claude/skills/whats-new-populate/`; hooked into workspace
  `CLAUDE.md` "Release policy" and `CWNG_goal_act` train-departs step.

## The intent (operator's words, distilled)
Avid CWNG readers — smart but **not technical** — should be able to discover what's
new without reading a GitHub changelog. Give them an in-app "What's New" page that
reads like Claude Code's release notes, but:
- written **for readers, not developers**;
- each entry says **what it is, why it exists (the problem / the "why"), and how to
  use it (the solution / the "how")**;
- **deep-links into the actual feature inside CWNG wherever possible** — the whole
  point is one-tap "show me," so a user might solve a problem they didn't know they had;
- simple, thorough, well-worded, context-rich, deeply humanized (see tone rules).

## UX placement
1. **Entry point:** add a `What's new` item to the Help ("?") menu in the SPA top bar.
   - File: `frontend/src/components/TopBar.tsx`, `HelpMenu()` (around lines 132–141).
   - Add a `<MenuItem>` with an internal `to="/whats-new"` (wouter `Link`, NOT `href` —
     internal nav, client-side; see the existing `to=` vs `href=` split in `MenuItem`).
   - Icon: a lucide-react glyph consistent with the set already imported (e.g. `Sparkles`
     or `Megaphone`). Keep it monochrome like the other Help items.
   - Label string must go through `t('What's new')` for i18n (see i18n section).
2. **Optional secondary nudge:** a small "•" unread dot on the Help button (and the
   item) when there's a release the user hasn't opened yet, keyed in `localStorage`
   to `constants.INSTALLED_VERSION` — same version-keyed pattern as the
   `cwng_newui_banner_dismissed_<version>` banner. Dot clears once they open the page.
   Keep it subtle; this is discovery, not nagging.

## The page
- **Route:** add `<Route path="/whats-new">` in `frontend/src/App.tsx` (authed branch,
  inside the main `<Switch>` near the other top-level routes ~line 123+).
- **Component:** `frontend/src/pages/WhatsNew.tsx` + `WhatsNew.module.css`.
  Use the **frontend-design** skill — match the existing design tokens (dark
  blue-charcoal surface, amber accent `--accent:#cc7b19`, CSS Modules, the card/section
  rhythm already used by Catalog/BookDetail). It should feel like part of CWNG, not a
  bolted-on doc. Consider: a vertical timeline of releases, newest first; each release
  is a collapsible group with its date + version; each feature is a card with a title,
  a 1–3 sentence human description, an optional "Try it" deep-link button, and a small
  category chip (e.g. Reading, Library, Sync, Account, Admin).
- **Deep-link buttons** route via wouter to in-app destinations, e.g.
  `/app/account` (account settings), `/app/discover`, `/app/shelves`,
  `/app/book/:id/annotations`, the reader, etc. Where a feature lives behind a setting,
  deep-link to that settings screen and name the toggle in the copy. If a feature has no
  navigable surface (e.g. a backend reliability fix), omit the button — don't fake one.

## Data source (single source of truth)
The feature log is **derived from `CHANGELOG.md`**, which already gets a symptom-first
`[Unreleased]` entry on every user-facing merge (see CLAUDE.md "Release policy"). The
What's New content is the **humanized, deep-linked** projection of those entries,
grouped by published release.

**Recommended shape:** a typed data file the page renders from, e.g.
`frontend/src/data/whatsNew.ts` —
```ts
export interface WhatsNewItem {
  title: string;            // human, benefit-led ("Find something to read tonight")
  body: string;             // 1–3 sentences: what + why(problem) + how(solution)
  category: 'Reading' | 'Library' | 'Sync' | 'Account' | 'Admin' | 'Under the hood';
  link?: { to: string; label: string };  // in-app deep link, omit if none fits
}
export interface WhatsNewRelease {
  version: string;          // e.g. "v4.0.170"
  date: string;             // ISO; rendered humanized
  items: WhatsNewItem[];
}
export const WHATS_NEW: WhatsNewRelease[] = [ /* newest first */ ];
```
Keeping it as data (not hand-written JSX per release) is what makes it
**AI-iterable** and lets the per-release skill append a block deterministically.

## i18n
- All static chrome strings (`What's new`, category names, "Try it", section headers)
  wrap in `t()` and get msgids via the SPA i18n bridge.
- The per-release feature copy itself is **English-authored**; translating every
  release's prose is out of scope (and would gate releases on translators). The page
  copy/chrome is localized; the feature descriptions are English. Document this clearly
  in the page (no surprise for non-English users — chrome is localized, entries are not).

## Tone / humanization rules (copy standard — applies to every entry)
Reuse the project's human-writing standard (memory: jellyfin-llm-tone, outreach-tighter-shape):
- Lead with the **reader benefit**, not the mechanism. "Your highlights now follow you
  between your Kobo and the web reader" > "Added KOReader annotation sync endpoint."
- Name the **problem it solves** in plain words ("you used to lose your place when…").
- Say **how to use it** concretely ("open any book → tap the ⋯ menu → Send to Kindle").
- No AI tells, no marketing fluff, no "we're excited to announce," no emoji-spam, no
  exclamation storms. Smart-reader register: warm, precise, concise.
- 1–3 sentences per item. If it needs more, it's two items.
- Credit nothing to specific contributors here (that's release notes / outreach); this
  page is the reader's-eye view.

## Make it a skill (so it's populated EVERY public release)
Create a Claude Code skill, e.g. `~/.claude/skills/whats-new-populate/SKILL.md`,
invoked as part of the release train (and referenced from CLAUDE.md "Release policy").
On each **public versioned release** the skill must:
1. Diff `CHANGELOG.md` (or git log) for everything user-facing **since the last
   published release tag**.
2. For each user-facing change, author a `WhatsNewItem` following the tone rules:
   benefit title + what/why/how body + category + best-fit in-app deep link (resolve a
   real route from `App.tsx`; verify it exists; omit if none fits).
3. Prepend a new `WhatsNewRelease` block (newest first) to the data file.
4. Be **comprehensive** — every shipped user-facing feature/fix since last release gets
   an entry; nothing silently dropped (mirror the changelog coverage).
5. Verify deep links resolve (route exists in `App.tsx`) before committing.
6. Ship in the same release so the new page reflects the release that announces it.

Add a one-line hook in CLAUDE.md "Release policy" / the release skill: **"Every public
release: run the What's-New populate skill so the in-app feature log is updated with all
user-facing changes since the last published release, deep-linked and humanized."**

## Verification (per Enterprise standard, when built)
- Playwright: Help menu → "What's new" opens the page on desktop + mobile; deep-link
  buttons navigate to the right in-app screens; unread dot appears on a fresh version
  and clears after opening.
- i18n: chrome strings localized under a session-authenticated locale.
- Render with an empty + multi-release dataset (no crash, sensible empty state).
