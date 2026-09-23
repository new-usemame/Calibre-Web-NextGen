# PDF Reflow runtime hardening — scoped specification

Base: `04a875b040943b25c709331ee81135166d274dd9`.  This change deliberately
does not alter source recovery, native glyph extraction, or publication.

## Problem and evidence

The actual book 569 job left a durable journal through `operation_audit.validated`
and then the application service was restarted.  Docker reported neither an OOM
kill nor a container restart.  That establishes a missing process-lifecycle and
build-boundary record; it does not establish a PyMuPDF crash as the cause.

The isolated exact-image replay completed.  Its apparent recovery-count mismatch
was resolved from its emitted `META-INF/reflow.json`: 74 pages used the OCR layer
and cache, of which 18 had OCR words and 56 had zero words.  `mode_pages` counts
the former; `recovered_pnos` intentionally counts only the latter subset with
words.  Future records must make both facts independently visible.

## This slice

1. Before EPUB assembly, durably record a privacy-safe recovery provenance
   digest and compact complete page census: page number, selected layer, reason,
   engine/language identity, geometry orientation, words, uncertainty, reuse,
   failure, and timing.  Record only already-produced metadata, never OCR text,
   cache paths, credentials, or arbitrary user paths.  The digest covers a
   canonical JSON representation of the same census and fixed recovery options.
2. Durably checkpoint assembly at bounded phase, source-evidence page, and figure
   crop intervals.  A long book cannot create one journal event per crop: checkpoints
   include each changed page and periodic crop sequence numbers, capped at a
   documented finite count.  A fatal native exit can therefore be localized to a
   phase/page/interval without making a multi-megabyte request journal.
3. Retain the long-running s6 service's last exit code and signal in a fixed,
   application-owned `/config/.runtime-supervision` record.  The recorder accepts
   only numeric supervisor values and never accepts a path from the environment or
   user input.

## Containment implementation (additive to the observability slice)

No crash cause is asserted by this change.  A successful worker-thread replay
does not clear the native-threading hypothesis, and it also does not exclude an
external signal.

Application conversion now uses `NativeDocument`, a supervised fresh interpreter
launched with `-I` and a fixed installed worker script. The worker opens its own
private PDF snapshot. It never inherits a live PDF/page object, web bootstrap,
provider client, ledger object, credential environment, or Python pickle.

The fixed operation vocabulary is open, survey, prepare, operations, and build.
Native extraction/recovery/assembly, per-page structural evidence/raster preparation,
and final native glyph/figure/source rendering execute in that process. The API's
page count and source survey also use it; quote preparation retains its existing
fresh-exec worker. Offline callers with actual PDF documents remain compatible.

The parent retains the existing structural orchestration and typed provider calls
entirely, rather than delegating a provider RPC proxy. This smaller boundary leaves
authentication, budgets, durable claims/reservations/reconciliation, typed request
validation, source-authority factories, approval/adoption and publication unchanged.
Only registered pure value types cross JSON; no dispatch instruction or secret does.
Child-built candidates still pass the parent's original source fingerprint checks,
EPUB validation, operation-emission audit and atomic publication transaction.

The protocol has an exact version, explicit class registry, duplicate-key/unknown-
field/nonfinite/depth/size rejection (256 MiB, depth 80), fixed manifest basenames,
no-follow regular-file checks, sequence binding, and a separate bounded control FD.
The source10 interface additions are explicitly round-tripped without alternate OCR
substitution. A candidate path from a reply cannot choose the parent's read path.
SourcePage authority is reissued from its pure provenance and checked identity in
each process; wire data alone never receives an adoption seal.

The child has its own process group. Cancellation is checked while waiting and
before every progress acknowledgment; cleanup stops only that owned group and
removes only the parent-created scratch directory. Native stderr is drained into
a 64 KiB rolling tail independently from protocol traffic, with exit code/phase
retained in the existing job ledger. Existing bounded build markers remain active.

This is crash containment, not an OS sandbox: the worker runs under the service UID,
and native memory exhaustion remains subject to the deployment's resource limits.
It receives no arbitrary output path but has OS access appropriate to that UID.
No historical cause is inferred from containment tests. Independent review and
composed source-policy tests precede deployment and an actual-book retry.

The implementation gates are:

- forced child termination yields a failed job, no server exit, no newly published
  EPUB, and no released-or-forgotten reservation;
- cancellation terminates only the owned child and cleans only its owned staging
  path;
- a successful child emission passes the same EPUB validation and source
  fingerprint/publication checks before the parent adopts it;
- malformed manifests, external paths, and unexpected child artifacts are
  rejected before any native work.

## Seen-red behavioural plan

- **Recovery census:** construct recovery provenance with an OCR zero-word page
  and an OCR word page; the durable event must distinguish `ocr_layer_pages=2`
  from `ocr_pages_with_words=1`, and its digest must change if an option or page
  provenance changes.  Removing the zero-word row or digest input makes this red.
- **Build trace:** drive the emitted progress contract across phases and many crop
  events; the journal must retain the first, page-transition, periodic, and final
  crop checkpoints while staying below the configured cap.  Replacing sampling
  with unconditional crop records makes the bounded assertion red.
- **Service exit:** invoke the pure recorder with supervisor exit/signal values;
  it atomically replaces the fixed record with numeric values.  Invalid values
  must be rejected rather than written.  Removing validation or atomic replace
  makes this red.
