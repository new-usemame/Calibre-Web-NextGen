# Annotation sync — design principles for a multi-device server

CWNG serves annotations to Kobo (Nickel), KOReader (`cwasync.koplugin`), the web reader, and
whatever comes next. The 2026-08-15 incident (three independent causes, 95 + 87 highlights
destroyed) was not bad luck; each cause was a *general* design mistake that happened to surface on
one device first. This document states the principles so the next device does not repeat them, and
records where the codebase still violates them.

Companion: `notes/KOBO-HIGHLIGHT-LOSS-ROOT-CAUSE-2026-08-15.md` (the measurements).

---

## P1. Never answer "you have none" when you mean "I don't know"

A sync client cannot distinguish those two, so it acts on the destructive reading. **Measured
consequence:** we forwarded a Kobo annotation download to Kobo's cloud, which has never heard of a
sideloaded book and replied success-shaped-empty; Nickel deleted 87 local highlights that existed
nowhere else.

**The rule:** an empty *success* is a positive claim that the set is empty. Only make it when it is
true and you are authoritative for that set. Otherwise refuse in a way the client can distinguish.

| Situation | Correct answer |
|---|---|
| Known entity, genuinely zero annotations | 200, empty set |
| Unknown entity / not authoritative | a distinguishable non-claim |
| Backend unavailable | explicitly transient (503 + `Retry-After`) |

**Status:**
- Kobo download — **fixed** (#1636): `handle_annotations` returns 503 + `Retry-After` for a book we
  own rather than proxying Kobo's empty answer. `cps/readingservices.py`.
- KOReader pull — **STILL VIOLATES THIS.** `pull_annotations` returns
  `{"annotations": [], "annotation_count": 0}` at HTTP 200 when the digest matches no book
  (`cps/progress_syncing/protocols/koreader_annotations.py:277`). The code comment already names the
  ambiguity. Not destructive with our current insert-only plugin, so it is a latent hazard, not an
  active bug — but it is the same mistake.
  ⚠️ **Fixing it is a compatibility problem, not a code problem.** Shipped plugins parse the current
  shape. Prefer an ADDITIVE discriminator (e.g. `"book_known": false`) that old clients ignore and
  new clients honour, over a status-code change that breaks installs in the field. Decide
  deliberately; do not "fix" it by changing the status code in a patch release.

## P2. Never destroy irreplaceable data to satisfy a validator

Classify every field before you guard it:
- **Irreplaceable** — highlighted text, note text, span anchors. The device is often the only other
  copy.
- **Derived / recomputable** — `content_id`, and anything a backfill can rebuild.

A validation failure on a derived field degrades **that field**, never the record.
`_upsert_annotation` did `return None` on a bad `content_id` and destroyed 95 highlights in 48
hours (#1531 → fixed by #1635). See `validator-must-not-discard-user-data` in project memory.

**Corollary: log the value you rejected.** The original warning said only "invalid content
location"; the diagnosis required a live DEBUG capture to learn the value was
`OPS/../OPS/chapter-017.xml`. A rejection message without the payload wastes the incident.

**Corollary: prove the guard is reachable in tests.** That gate was dead code in every test because
the fixture book had no `uuid`. A guard nothing exercises is a guard nobody reviewed.

## P3. Be liberal in what you accept from a device, strict about what escapes

Client path data is produced by closed firmware and will be untidy. Normalize what is unambiguous;
reject only what is genuinely unsafe. `_chapter` rejected every dot segment, so a Kobo's
`OPS/../OPS/chapter-017.xml` — unambiguously `OPS/chapter-017.xml` — was refused (#1638 → normalize
contained traversals, still reject anything escaping the container).

The security boundary is **escaping the container**, not cosmetic variance.

## P4. Fix the producer, not the symptom

Repairing 88 broken `Bookmark` rows made them render — and the very next highlight broke again,
because the *book* still produced bad anchors. Order of operations:

1. Fix the producer (the package: #1637 normalizes escaping OPF hrefs at conversion).
2. Re-deliver (bump `books.last_modified`).
3. Only then repair existing rows.

⚠️ And repairing rows first is actively dangerous: well-formed rows became eligible for the P1
deletion that malformed ones were invisible to. **Server first, device second.**

## P5. Device-agnostic core, device-specific edges

The annotation model, the package normalizer, and any user-notice mechanism must not encode "Kobo".
Device-specific behaviour belongs at the protocol boundary (`cps/readingservices.py`, `cps/kobo.py`,
`cps/progress_syncing/protocols/`). A package defect that breaks Kobo anchoring is a *packaging*
problem and is fixed in the conversion pipeline, where every device benefits.

## P6. Make gaps explicit and queryable

Never encode "unknown" as a degenerate value of a real field — it is indistinguishable from data to
every consumer that did not know to look. Use an explicit, validated discriminator. Precedent:
`position_type` for unanchored notes (`unanchored-note-cannot-round-trip-to-kobo` in memory).

## P7. Repairs are additive, reversible and proven isolated

Before mutating a user's device or library: back up, verify the backup byte-for-byte, scope the
write by an explicit key, verify each target exists, and hash everything you did *not* intend to
touch before and after. The 2026-08-15 device repair rewrote 88 rows and proved the other 2928 were
byte-identical. Never `DELETE` where an `UPDATE` or an additive row will do.

---

## Open work

- **KOReader pull discriminator** (P1) — additive, compatibility-first. Not yet done.
- **Repair pass for already-converted KEPUBs** — #1637 only fixes *new* conversions. Existing
  libraries keep the defect until re-converted. Mirror `cps/tasks/kepub_backfill.py` and its
  `config_kobo_kepub_backfill_completed` idempotence-flag pattern.
- **User notice, generic and dismissible** — the server can repair the package but CANNOT repair
  `Bookmark` rows already broken on a device, so the user must be told. Build it as a
  reusable per-user notice/dismissal mechanism keyed by a notice-type string, not a single-purpose
  column, so the next device quirk reuses it (P5, P6).
