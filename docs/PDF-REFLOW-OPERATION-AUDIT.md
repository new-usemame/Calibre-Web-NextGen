# Applied source-operation audit

Each conversion's private job journal contains `operation_audit` records with
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
