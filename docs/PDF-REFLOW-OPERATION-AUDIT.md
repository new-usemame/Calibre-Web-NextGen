# Historical applied source-operation audit

This schema describes the earlier structural proposal/approval route and retained
historical job evidence. New application conversions use the vision page-reading
route described in [PDF Reflow application route](PDF-REFLOW-APP-ROUTE.md); their
page census and publication receipts are recorded with the vision job summary.
The historical schema below does not describe newly requested page readings.

Each historical conversion's owner-scoped job journal contains `operation_audit` records with
schema `applied-source-operations-1`. They follow the job's existing owner/library
access policy and retention; they are not embedded in the downloadable EPUB or
returned as additional public source text. The schema retains hashes, bounded
candidate metadata and request references, never model-view text or source images.

The records distinguish three observations:

* `validated`: source-bound plans returned by the approval/admission stage. Each
  operation includes book ID, source PDF hash, zero-based PDF page, candidate ID,
  kind, element/range, snapshot, source revision, raster hash, selected UTF-8 text
  hash, and actual stage request/attempt or cache references. This is not evidence
  that a wrapper reached an EPUB.
* `emitted`: after the builder and EPUB validation, inspection of actual chapter
  XHTML finds the role and text after an explicit source-page marker in that
  same chapter. A chapter prefix without a local marker remains unverified,
  including a legitimate continuation whose source page is not explicit. Each row is `verified`,
  `not_found`, or `ambiguous`; verified rows include entry and element ordinal.
  Qualification badges remain present; comparison accounts only for the renderer's
  known qualification of an uncertain atomic source marker. The record binds the
  candidate EPUB SHA-256 and byte length. This is not a publication receipt.
* `published`: the task completed sample placement or full-library publication,
  with the same artifact hash. The existing full publication journal remains
  authoritative across crashes. Absence of this additional receipt means unknown
  publication status until that journal is reconciled, not proof of nonpublication.

Only admitted operations appear. Rejected/abstained and unreviewed pages remain in
existing structural stage/summary records; they cannot acquire an applied role
merely through a provider approval. Missing or ambiguous emitted matches require
review and do not assert semantic correctness. Independent source adjudication is
still needed for every newly adopted source unit.

Audit writing occurs after provider processing. Failure keeps normal paid-response
cache and financial-ledger evidence: a subsequent authorized job reuses answers
rather than treating them as unsent. No audit record changes request serialization,
source preparation, quote cache identities, billing, or the provider route.

## Reading assistance and source evidence

The EPUB distinguishes three publication states. Proved words remain ordinary
text. Plausible but unproved words remain live, visibly underlined, with original
print appended to the word or its contiguous reading run. Undecodable glyph codes
and incomplete surviving words retain print alone. A flag is an unresolved
reading, including when the candidate happens to be correct; it never upgrades
an adjudication to a proved correction. Supported corrections retain their
existing linked notices.

Inspection links retain the complete original crop. The optional gray inline
preview follows the source line's baseline and x-height and is excluded from
source-occurrence coverage evidence. Its empty alternative text avoids speaking
the live candidate twice. A source word retained only as print has an explicit
inspection label. Theme or reading-system image transformations need visual
verification; browser rendering does not prove Kindle conversion behavior.

Representation equivalence covers presentation ligatures, quote forms and soft
hyphens, preserving accents and macrons. Repeated-word proof is currently bounded
to complete matching ink on the same page, earned from independent readings. It
does not infer an authoritative font map or extend spelling proof across a book.
A dictionary may rank already observed reading candidates for flagged assistance;
it cannot certify the print, change a proved word, or decide between two valid
words. Native candidate assistance keeps its source reading unless the existing
pixel adjudication independently proves a correction.
