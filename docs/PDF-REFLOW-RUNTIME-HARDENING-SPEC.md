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

Composition with the full source10.1 corpus exposed JSON memory amplification:
encoding Book567's preparation result with the original tagged-tree encoder was
OOM-killed in an isolated 2 GiB container. Protocol version 2 therefore streams a
postorder value DAG into one valid JSON document, preserving shared raw/recovery
page references. The decoder constructs one node at a time instead of first
parsing a second full tagged graph. References select only previously decoded
values, never forward/cyclic values or executable types; original size/depth and
class/field validation remain. No v1/v2 interchange is accepted. Parent and child
must come from the same installed composition.

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

## Shared native capacity profile (post-564 resource incident)

The 540-page cached Book564 run peaked at 1.908 GiB **aggregate app memory**;
earlier global OOM killed both child and parent. Its later successful run used
three >=2 GiB available-memory readings and a 512 MiB stop guard. These are
observations, not a universal bound or a safe 2 GiB container-size claim. The
separate SIGBUS cause is still unknown. Parent s6 now enables Python fatal stacks
on stderr and disables core dumps; the existing finish hook retains exit facts.
No stack locals, credentials or process memory dumps are collected. Configure
bounded platform log retention; this instrumentation is not a SIGBUS fix.

`native_resources.Lease` now gates BOTH native launchers (`NativeDocument`, used
by conversion/page count/survey, and `quote_preparation.measure_isolated`). One
heavy work item per Reflow state root may be live, across service processes. The
permanent `native-resource.lock` uses nonblocking flock; busy fails immediately,
not an unbounded wait. Do not unlink/replace it, use separate state roots for the
same resource budget, or put it on storage without reliable cross-process flock.
The child inherits its open descriptor, so owner-process death does not release
the slot while that child still lives. Unkillable/orphaned work can retain the
slot: explicit operator diagnosis is safer than guessing a PID is stale. Normal
cleanup reaps only the owned process group before closing the parent descriptor.
Supervised OCR stays in that group and explicitly inherits the same lease FD;
standalone OCR still owns a separate invocation group. The launch-only
`REFLOW_NATIVE_LEASE_FD` environment entry is internal descriptor transport,
not an operator capacity setting. A malformed inherited descriptor fails OCR
before launch. Executables that deliberately detach or discard descriptors are
outside this cooperative contract; this is not an OS sandbox.
This is cooperative service-instance admission, not distributed host arbitration.

Configuration is trusted deployment environment, not request data:

| Variable | Default | Meaning |
|---|---|---|
| `REFLOW_NATIVE_CAPACITY_MODE` | `checked` | `checked` or explicit `serialize-only` |
| `REFLOW_NATIVE_ADMIT_MIB` | 2048 | Free headroom required before launch |
| `REFLOW_NATIVE_RESERVE_MIB` | 512 | Stop below this free headroom |
| `REFLOW_NATIVE_SCRATCH_ADMIT_MIB` | 2048 | Free Reflow scratch-volume space before launch |
| `REFLOW_NATIVE_SCRATCH_RESERVE_MIB` | 256 | Stop below this scratch-volume reserve |

Numeric values must be positive integers, with each reserve below its admission
threshold. Checked mode takes three readings 100 ms apart, then checks again
after snapshot copying immediately before launch. It polls reserve during native
waits at most every 500 ms and forces fresh readings at parent pipeline/provider
stop and pre-publication checkpoints. Failure latches for that work item; no
automatic retry, cap change, unknown-hold release or partial publication follows.
No standing monitoring thread/service is created. A request already posted to a
provider is not undone: its existing billing/unknown-outcome semantics still apply.

On Linux checked mode reads MemAvailable and applicable visible cgroup-v2
memory.max/current values, including visible ancestors, and uses the minimum
headroom. It does not count swap or treat MemAvailable as a container limit.
All applicable mount views are walked: adding a subtree view cannot conceal a
limiting ancestor visible through a full mount. Missing controller files at a
non-root mount remain unknown even when `cgroup.type` says `domain`.
At a proven cgroup2 mount hierarchy root `/`, absence of both memory limit/usage
files is legitimate; readable `cgroup.controllers` must advertise memory.
`cgroup.type` is a non-root interface and is not required at the actual root.
Partial pairs, unreadable metadata and missing non-root pairs still fail closed.
Missing/malformed data, v1/hybrid memory controllers and non-Linux meters are
unsupported and **fail closed**. Namespace-hidden ancestor limits cannot be
discovered: expose the effective envelope or reserve it externally. Explicit
`serialize-only` retains the shared slot but skips RAM AND scratch measurements;
it is suitable only for a separately capacity-managed deployment/development
runner, never evidence that memory protection was tested. Unit tests inject
ample measurements while retaining real checked admission and flock; targeted
tests replace them with low/unknown signals and synthetic proc/cgroup files.

Budget parent/web activity, native work, provider JSON, caches and foreign
services separately. Scratch thresholds are floors, not a space reservation:
allow for PDF snapshot, IPC graph, source rasters, candidate EPUB and publication
backup, and provision the library volume separately if mounted elsewhere. The
observed 14.66 MB PDF produced a 296.42 MB EPUB; compressed input size is no RAM
or output-size predictor. A container limit caps the whole service, not just the
child, and does not reserve memory for the parent. Sampling cannot reliably beat
every sudden allocation/OOM or prevent unrelated workloads consuming capacity.

Refused conversion tasks remain failed/owned until normal task finalization;
preparations report `native_capacity_unavailable` (or cancelled with
`native_capacity_stopped`); estimate/page-count busy conflicts are HTTP409 and
low/unknown capacity is HTTP503, not
a bad-PDF diagnosis. There is no queued promise to auto-retry when space returns.
Root/deployment owner must validate the final composed image/profile on a normal
representative workload, with bounded resource/exit evidence and original-file/
financial checks. Independent review and actual reader gates are still separate.

Kernel contracts: [cgroup v2 hierarchy and memory controller](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
and [flock open-file-description lifetime](https://man7.org/linux/man-pages/man2/flock.2.html).

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
