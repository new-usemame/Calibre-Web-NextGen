# KOSync text anchors: positions for word-based reading clients

Status: built 2026-10-03 on `feat/reading-position-sync`. Code: `cps/services/text_anchor.py`,
`cps/progress_syncing/protocols/kosync.py` (`_locate_anchor`, `_anchor_for_record`,
`_book_for_numeric_document`), `koreader_xpointer.spine_reading_texts`. Tests:
`tests/unit/test_kosync_text_anchor.py`.

## Problem

Some KOSync clients flatten a book to plain words. Speed readers, TTS players and plain-text
renderers keep their place as a word index. They cannot produce a crengine XPointer or an epub.js
CFI, and their percentage is computed over a different tokenisation than KOReader's or a Kobo's,
so a percentage hand-off is often pages away from where the reader actually was.

## Contract (additive; stock KOSync clients see no change)

- `GET /kosync/users/auth` adds `capabilities`. KOReader reads only the auth status.
- `PUT /kosync/syncs/progress` accepts `position_kind: "percentage"` with no `progress` and an
  optional `anchor: {text, before, after}`, where `text` is the word at the position and
  `before`/`after` are up to 8 words on each side. Malformed input is refused with 400 and nothing
  is stored.
- `GET …/progress/<document>?position_kinds=locator,percentage,anchor` adds `anchor` when the
  winning position is provably a place in the library EPUB.
- `document` may be a decimal Calibre book id. It resolves only to a book the account may open
  (`get_filtered_book(user=…)`).
- An anchor is the book's own text, so placing one or receiving one is a content read. The account
  must be able to see the book and hold the viewer or download role. Otherwise the push is stored
  as a plain percentage and GET carries no `anchor`.
- `percentage` must be a finite number in 0–1 (or 0–100). GET always returns it as 0–1.
- When the anchor is located, the stored and compared percentage is where those words are in the
  library EPUB's text, not the client's own figure. Each client counts over its own text (with or
  without front matter, by words or by pages), so the same sentence has a different figure in each.
  Without this, a client that counts more words could win furthest-wins from behind. Measured on
  Metamorphosis, the derived figure is within 3% of KOReader's own page fraction on 115 recorded
  pages (mean −0.6%); a word-count client was 7% off. Finished stays the client's call in both
  directions. A derived figure never reaches the finished threshold (99%) unless the client's own
  figure does. A client at ≥ 99% keeps that figure even where back matter puts its last words lower
  (the end of Metamorphosis' story is at 86% of the file). An unanchored push keeps the client's figure.
- An anchor that GET serves always passes the PUT limits (`text` ≤ 200, `before`/`after` ≤ 600
  characters). Context is trimmed by whole words, dropping the words furthest from the place. A
  script with no spaces between words has no word to name, so it gets no anchor.

## The fold (clients must implement exactly this)

The fold applies per Unicode scalar, iterating code points rather than grapheme clusters:

1. Drop it if it is whitespace (`str.isspace`) or one of U+00AD, U+200B, U+200C, U+200D, U+2060,
   U+FEFF.
2. U+2010–U+2015 and U+2212 become `-`.
3. U+2018–U+201B become `'`, and U+201C–U+201F become `"`.
4. Anything else is NFKD-decomposed, has its nonspacing marks (category Mn) dropped, and is
   lowercased with the default Unicode mapping (`str.lower`, not `casefold`).

Shared vectors: `FOLD_VECTORS` in `tests/unit/test_kosync_text_anchor.py`.

## How it works

- **Write.** The anchor is matched against the library EPUB's folded solid text. Folding means:
  whitespace, soft hyphens and zero-width characters removed; dashes and curly quotes unified;
  NFKD with combining marks dropped; lowercase. The longest needle is tried first: before+text+after, then text+after, then
  before+text. A repeat is chosen only when the client's percentage puts it clearly nearer (by more
  than 5% of the book) than every other repeat. If the anchor is placed, the row stores the library
  EPUB XPointer and journals it under the library file's partial MD5. That is exactly what a
  KOReader holding the library file would report, so KOReader, the web resume and reading sources
  need no new code. If it is not placed, the row stores the percentage-only sentinel.
- **Read.** The winning row resolves to an XPointer in the library EPUB, or to nothing:
  - KOReader xpointer: only when the journal shows it was reported from the library file's digest;
  - web CFI or Kobo span: through the existing `_exact_xpointer` proofs, with the library digest as
    the requesting file.
  `anchor_at` then turns the XPointer into words using `spine_reading_texts`. That function counts
  characters the same way as `spine_solid_texts`, and it refuses the book if the two counts
  disagree.
- **Who wins** is unchanged: furthest position across devices, and a same-device rewind is allowed.
  The exception is a percentage the server could not place. When it only ties another device's row,
  it does not replace that row, so an echo of a pulled percentage cannot erase an exact locator.
- **Cost.** The folded book is cached (8 books, keyed by path, mtime and size) at about 10 bytes
  per character. A needle with 64 or more hits is not placed, because the nearest repeat may be
  among those not counted.

## Measured

Metamorphosis, 116 Kindle page positions, xpointer → anchor → xpointer:
- 114 return to the same place;
- 1 is an SVG cover with no text;
- 1 is Gutenberg licence boilerplate that repeats, which is correctly refused.

## Known limits (deliberately deferred)

- A device holding a metadata-embedded copy (a different digest, but the same text) is not proven
  to share XPointers. This is the same rule as the existing web↔KOReader conversion.
- KOSync → Kobo is still percentage-only. The Kobo bookmark keeps its own last span while
  `ProgressPercent` advances. Writing an exact span (`kepub_alignment.xpointer_to_span` exists but
  is unwired) is a follow-up.
- The device kind of an anchor client is `koreader`, because it is registered through the KOSync
  path. Reading sources show the client's own `device` name.
- A push without `device_id` stores the located XPointer but writes no device journal. That
  client's own place then never comes back as an anchor, and the web reader is not given it
  exactly.
- `?position_kinds=anchor` without `percentage` is the old locator-only request, so the
  percentage-only rows (a web or Kobo place) are not served to it. Ask for
  `locator,percentage,anchor`.

---

# Annotations as text quotes (highlights, notes, bookmarks)

The same idea for `/kosync/syncs/annotations`. A word-based client cannot name a highlight by
XPointer, CFI or KoboSpan, but it can name its words. It sends a **text quote** (W3C
TextQuoteSelector's `exact`, `prefix`, `suffix`), and the server places it in the library EPUB.

## Contract (additive; KOReader's plugin sees no change)

Advertised as `annotations_text_quote` in `GET /kosync/users/auth`.

`PUT /kosync/syncs/annotations`. Each entry in `annotations` may carry:
- `source: "textquote"`, the provenance of this client kind;
- `type`: `highlight`, or `dogear` for a bookmark (a point at one word);
- `text_quote: {exact, prefix, suffix}`. `exact` is at most 4000 characters, the context at most
  600 characters a side, and `exact` must fold to something non-empty;
- `percentage`, a fraction from 0 to 1. It only chooses between repeats, as for positions.

`document` may be a decimal Calibre book id, which resolves only to a book the user may see.
`delete_source: "textquote"` names this client as the deleter. A push may delete only rows of its
own source, named in `deleted` or sent inline as `hidden: true`. A push without `delete_source`
is KOReader's, as before. The response adds `resolved` and `unresolved`, the quoted
`annotation_id`s by outcome.

`GET /kosync/syncs/annotations/<digest or book id>?text_quote=1`. Every row carries `text_quote`:
- the stored quote, for a row the client sent itself;
- otherwise one derived from the row's anchor;
- `null` when there is none.

Rows carry `content_revision` and `server_modified_at`. Both advance on every content change,
from any writer, including device pushes, which did not advance them before. `last_synced` also
moves when the server only re-derives an anchor, so it is not an edit clock. Tombstones
(`hidden: true`) from every source are served, as before.

## How it works

- **Placing.** `text_anchor.place_quote` uses the same fold and the same needle and repeat rules
  as `place`, then makes a range. The start is the first character's XPointer. The end is just
  past the last character, the same convention as CFI-derived XPointers. The book's text between
  the two must fold equal to `exact`. A quote ending inside a ligature is refused rather than
  widened.
- **Storing.** A placed quote is stored as `position_type: koreader_xpointer` with
  `highlighted_text` set to the book's own words. KOReader compares those words before drawing.
  The web reader converts the pair through the existing `_compute_koreader_cfi`. An unplaced
  quote is stored as `position_type: text_quote`, with `exact` as its text. That is an explicit
  gap (P6 of ANNOTATION-SYNC-PRINCIPLES): the web reader reports it as unresolved, and nothing
  guesses. A later push that cannot be placed never erases an anchor an earlier push found. The
  quote itself is kept in `annotation.text_quote` as JSON. The stored quote names the place it was
  pushed for. When another reader moves the highlight, the stored quote is cleared, and pulls derive
  the new words. Otherwise the client would send back the old words and move the highlight back.
- **Other readers' rows stay theirs.** A quote pushed for a row of another source that already has
  an anchor (a CFI, a KoboSpan or an XPointer pair) is dropped, and the row's anchor and words are
  kept. Note and colour edits still apply, and the id is reported as `resolved`.
- **Naming on pull.** `quote_at` reads the words of a range.
  - A KOReader row uses its XPointer pair in the library EPUB.
  - A web-reader row uses its CFI, when that CFI is against the EPUB.
  - A Kobo row uses its start span, through `kepub_alignment.span_text_range`, because the KEPUB
    and the EPUB hold the same text. The highlighted words must start in that span exactly once.
    The Kobo's character offset counts a DOM this server does not rebuild.

  A derived quote is served only when it folds equal to the row's `highlighted_text`. An anchor
  into another copy of the book therefore gives no quote rather than wrong words. Derivation runs
  only on `?text_quote=1`, off the request greenlet. Quotes stored by the client itself are always
  served.
- **Access.** Placing and naming read the book's text. They need the viewer or download role and
  visibility (`kosync._readable_epub`), as anchors do. Without that right a quote is stored
  unplaced and pulls carry no derived quotes.

## Known limits (deliberately deferred)

- Native Kobo (Nickel) sync draws only KoboSpan rows. A placed quote, like any KOReader highlight,
  is not converted to spans for Nickel. `kepub_alignment.xpointer_to_span` exists, and wiring it
  into the Kobo annotation serve path is a follow-up that serves both sources.
- A Kobo dogear has no text, so it reaches the client without words. A client bookmark
  (`dogear`) is a point, so neither KOReader (whose plugin draws only rows with text) nor the web
  reader draws it. Both keep it and round-trip it.
- Unanchored notes (`position_type: unanchored`) carry no quote. The client skips them for now.
- A quote whose words cross two spine items has no single XPointer range and stays unplaced.
- Delete authority is declared by the pushing client (`delete_source`), not derived from its login.
  It protects one reader's rows from another reader's sync logic, not from the account itself,
  which can delete all of its own annotations anyway. An unknown `delete_source` on a push that
  names no deletes falls back to KOReader's, as before, so inline `hidden` on a client's own rows
  is then refused.
