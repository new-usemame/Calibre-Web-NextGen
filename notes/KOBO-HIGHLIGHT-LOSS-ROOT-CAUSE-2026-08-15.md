# Kobo highlight loss — two independent root causes, both measured on hardware

**Date:** 2026-08-15 · **Reporter:** household instance · **Symptom as reported:**
*"On a Kobo reading a KEPUB, I highlight something, leave the book, come back, and the highlight is gone."*

Everything below was measured, not inferred. The device DB was read over USB from
`/Volumes/KOBOeReader/.kobo/KoboReader.sqlite` (copy it off first — `sqlite3` `mode=ro`
against the mounted volume fails with *unable to open database file*).

---

## Cause 1 — highlights never render: a `../` OPF href splits the chapter identity

Kobo identifies a chapter as `<book-uuid>!<opf-dir>!<href>`. A `Bookmark` renders only when
`Bookmark.ContentID` **exactly equals** a `content` row with `ContentType=9`. No fuzzy matching.

The affected book's OPF (`OPS/epb.opf`) declared the EPUB3 nav at the ZIP ROOT:

```xml
<item id="nav" href="../nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
```

Nickel joins `opf_dir + href` **without normalizing `..`**, so:

```
nav path        = "OPS/../nav.xhtml"
nav's directory = "OPS/.."
nav link        = "OPS/chapter-017.xml"
joined          = "OPS/../OPS/chapter-017.xml"   <- what got written to Bookmark.ContentID
```

Both identities are visible side by side in `content`:

| ContentType | ContentID |
|---|---|
| 9 (chapter, used for rendering) | `<uuid>!OPS!chapter-017.xml` |
| 899 (TOC entry) | `<uuid>!OPS!../OPS/chapter-017.xml-2` |

All 88 of the book's `Bookmark` rows carried a form matching neither table → **0 matches** →
nothing drawn. (Not literally the `ContentType=899` value either — see Cause 1b below.)
A control book returned `matching content row exists: 1`; this book returned `0`.

**Census query — orphaned highlights, device-wide:**

```sql
select b.VolumeID, count(*) n,
  sum(case when exists(select 1 from content c where c.ContentID=b.ContentID) then 1 else 0 end) matched
from Bookmark b group by b.VolumeID;
```

907 of 3016 were orphaned.

🚨 **CORRECTED 2026-08-16 — the original reading of 810 of those was WRONG, and the error is
instructive.** This note first said Iliad (584), King in Yellow (220) and Republic (6) had *no*
`ContentType=9` rows because the book had been removed from the device, and were therefore not
repairable. **All 810 are repairable**, and the books were never gone.

The mistake was the probe: chapter rows were looked for with `ContentID LIKE VolumeID || '%'`, which
returns zero for a `file:///mnt/onboard/...` volume **regardless of whether the book is present** —
because those volumes' chapter ids are not prefixed by the volume path. "No rows found" was read as
"book removed" when it meant "this query cannot see this volume shape".

**The control that settles it: the Odyssey.** Same `file:///` volume shape, 400 bookmarks,
**400/400 rendering**, and zero fragments. A "book is gone" explanation cannot survive it.

The real second cause is a **fragment-anchored TOC** — see below. Verified on the same backup:

| volume | bookmarks | anchored | carry `#` |
|---|---|---|---|
| Iliad | 608 | 24 | **584** |
| King in Yellow | 221 | 1 | **220** |
| Odyssey | 400 | **400** | 0 |
| three others | 873 | 873 | 0 |

```
fragmented: 810     repairable (exact ContentType=9 match after stripping '#'): 810
```

**Generalises:** a negative from a query is only as good as a positive control run through the same
query. The `LIKE VolumeID || '%'` probe was never validated against a volume known to work.

---

## Cause 1b — a fragment-anchored TOC (wider than the `../` case)

The `Bookmark.ContentID` matches **neither** the chapter row **nor** the TOC row. It is a *third*
form — the opf directory joined to the **raw TOC href**, without the `-N` disambiguator the
`ContentType=899` rows carry:

| what | ContentID |
|---|---|
| `ContentType=9` (what rendering looks up) | `…!OEBPS!…541-h-0.htm.xhtml` |
| `ContentType=899` (the TOC entry) | `…!OEBPS!…541-h-0.htm.xhtml#pgepubid00005`**`-2`** |
| **`Bookmark.ContentID`** | `…!OEBPS!…541-h-0.htm.xhtml#pgepubid00005` |

`matches_899 = 0/3`, `matches_9 = 0/3`. **This also sharpens Cause 1 above:** those Flatland
bookmarks were not carrying "the TOC-derived form" in the 899 sense either — the 899 row was
`<uuid>!OPS!../OPS/chapter-017.xml-2` while the Bookmark was `OPS/../OPS/chapter-017.xml`. Both
bugs are the same third form. **ASSUMED:** the exact derivation rule; the two observed cases differ
in whether the `<uuid>!<opf-dir>!` prefix survives, and that is not established.

The exact match against `ContentType=9` therefore fails:

```
Bookmark.ContentID     <uuid>!OEBPS!..._541-h-0.htm.xhtml#pgepubid00005
content ContentType=9  <uuid>!OEBPS!..._541-h-0.htm.xhtml
```

Measured on a Clara BW / 4.42, both books converted by current `main`: **1984** — 6 highlights, 6
anchored, TOC carries 0 fragments. **The Age of Innocence** — 3 highlights, **0 anchored**, all 42
TOC links carry a fragment. Both OPF manifests are clean.

⚠️ **The defect is entirely in the NCX, so #1637 does not touch it, and we ship these today.**
**92 of 216 books (42.6%)** have a fragment-anchored TOC — against #1637's 5 of 216.

### Two fixes that look obvious and are both wrong

**Stripping fragments from the NCX is NOT a fix.** The fragment is load-bearing navigation:
collapsing it would put Age of Innocence's 42 TOC targets onto 7 documents, and Dune's 1,780 onto a
handful. That trades invisible highlights for a broken table of contents.

**Normalising server-side does NOT fix rendering.** #1638 normalises the `content_id` *we* store,
which is right for our records, but the row Nickel draws from lives on the device. No server-side
change can make its exact match succeed.

**Why 1984 works** is the clue to the real shape: it was already split into per-chapter files, so
every TOC href targets a whole document and carries no fragment. The discriminator is not "the TOC
has fragments" but **"the TOC targets a position inside a document rather than a document"**.

So the candidate fix is splitting spine documents at TOC anchor targets during conversion — the
shape already hardware-proven by 1984. ⚠️ **Not yet designed or shipped, deliberately:** regenerating
a KEPUB shifts KoboSpan ids, so doing this to 92 of 216 books in a live library is a mass
re-anchoring event that would invalidate highlights users already hold. Any such fix needs a story
for books that already carry highlights, and probably applies to new conversions only.

**The 810-highlight recovery is independent of all of this** and stands alone: stripping the
fragment from `Bookmark.ContentID` re-anchors every one, with no conversion change.

**Library scan:** 5 of 216 books (2.3%) carry an escaping OPF href, every one `../nav.xhtml`.
Perfect correlation on the instance: every book with one has **zero** stored annotations ever;
all 604 stored annotations come from books without one.

**Repair** (device in USB mass-storage mode, Nickel not running): `UPDATE Bookmark SET ContentID`
to `<uuid>!<opf-dir>!<normpath(href)>`, scoped by `VolumeID`. Verify each target has a
`ContentType='9'` row first; hash every other volume's rows before/after to prove isolation. PK is
`BookmarkID`, so rewriting `ContentID` cannot collide. 88/88 restored this way.

**Durable fix:** normalize escaping manifest hrefs during EPUB→KEPUB conversion (PR #1637).

---

## Cause 2 — highlights are then DELETED: the annotation download returns an authoritative empty set

This is upstream [calibre-web #2610](https://github.com/janeczku/calibre-web/issues/2610), open
since 2022 and never root-caused because nobody had read the device DB during the event.

CWNG advertises itself as `reading_services_host` whenever Kobo sync is on, then forwards
`GET /api/v3/content/<uuid>/annotations` to `readingservices.kobo.com`. **Kobo's cloud has never
heard of a sideloaded book**, so it answers with a success-shaped empty set — and Nickel acts on
that by deleting every local `Bookmark` row for the book.

```
11:58   88 highlights present and correctly anchored (after the Cause-1 repair)
12:07   one sync:  1 annotation PATCHed up
                   GET .../annotations?limit=100  -> proxied to Kobo
after   0 Bookmark rows for that book
```

**87 deleted, never uploaded.** The server held exactly 1 of 88. For a sideloaded book the device is
frequently the only copy, so a 200-empty here is a **destructive operation even though the verb is
GET**.

⚠️ **Repairing Cause 1 EXPOSES annotations to Cause 2.** While the ContentIDs were malformed the
rows were invisible to the sync reconciliation and survived; making them well-formed made them
eligible for deletion. **Fix the server before repairing a device.**

**Fix (PR #1636):** when the entitlement resolves to a book we own, do not proxy the download
direction — return **503 + `Retry-After`**, the one response that cannot be read as "you have none".
Content we do not own still proxies (there Kobo's cloud genuinely is authoritative); PATCH upload is
untouched.

Deliberately **not** answering with our own snapshot: an internally-complete server view would tell
a second device to drop annotations it has not uploaded yet, and a regenerated KEPUB shifts KoboSpan
ids so a correct-looking snapshot installs misplaced anchors.

---

## Cause 3 (ours) — the server was discarding every Kobo highlight

Independent of the device, #1531 added content-id validation to `_upsert_annotation` whose failure
path was `return None`, discarding the whole annotation over a derived, nullable, recomputable
field. Deployed 2026-08-13 18:38:11; first discard 18:40:37; **95 highlights across 95 distinct ids
destroyed in under 48 hours**, zero annotations stored for any book in that window.

It shipped because the gate was dead code in tests: the dispatcher fixture's `_book()` is a
`SimpleNamespace` with no `uuid`, so the branch was never entered.

Fixed in #1635 (never discard) and #1638 (normalize contained traversals so `content_id` is correct
rather than NULL). The validator was *right* that the value was malformed — it was the canary for
Cause 1 — but it was wrong to charge the user for the discovery.
