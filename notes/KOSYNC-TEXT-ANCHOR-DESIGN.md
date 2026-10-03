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
