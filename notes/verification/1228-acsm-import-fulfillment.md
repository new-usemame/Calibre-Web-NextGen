# ACSM import-hook fulfillment (#1228)

ACSM is an acquisition ticket rather than an ebook-convert input. Ingest now asks the installed Calibre FileTypePlugin import hooks to fulfill a private copy of the ticket, validates a materialized EPUB/PDF, and sends that book through the existing guarded Calibre import transaction. The user's original ticket remains the receipt identity.

## Product decisions

- Fulfillment runs independently of Auto-Convert. Off imports the actual EPUB/PDF. On applies optional conversion to that book; failed optional conversion retains the fulfilled format. The actual resulting format determines the conversion ignore list.
- The staged input and materialized book keep the ticket basename. This preserves ordinary filename-derived metadata when a PDF has no embedded title and prevents two unrelated tickets from becoming the same generic `fulfilled` book.
- The downloaded book is durably staged under the config volume's `processed_books/acsm_fulfilled/<ticket SHA-256>/`. A manifest binds the ticket digest, output digest, filename, and format. Retry validates and reuses that book instead of fulfilling again. Confirmed import receipts remove only the acknowledged entry; damaged or interrupted entries retain the original and recoverable files for manual inspection.
- A missing or rejected plugin never creates an ACSM library row. The plugin's own reported failure is retained in the service log and the original ticket is backed up. Failed backup preserves the watched original. Import exceptions preserve both ticket and durable book.
- Plugin loading retains its existing explicit opt-in. No third-party plugin, Adobe account, credential, network endpoint, or dependency is bundled. Plugin code runs with the permissions of the existing Calibre runtime; this change does not sandbox it.

## Observed evidence

Both recovery and filename regressions failed before the repair. Independent fresh-process verification raised an actual precommit import exception, cleaned the conversion temp directory, then retried in a new Python process with fulfillment forbidden: the existing book bytes were imported and the hook call count remained one. Actual native Calibre 9.0 imported two metadata-free PDFs under distinct ticket-derived titles and IDs with automerge `ignore`. Manifest-publication fault injection, changed digests/format, traversal filenames, and a book symlink all preserved the original and stopped before another hook call.

Complete Linux images built from this worktree ran an owned diagnostic FileTypePlugin against private config/library/ingest volumes. The final restart/recovery used the current-main image; the earlier watcher image had identical fulfillment/ingest runtime files. The diagnostic plugin makes valid PDF fixtures and has no Adobe network or account behavior. Actual watcher flows established:

1. With plugin loading off, a diagnostic ACSM reached the failed backup, the installed plugin did not execute, and no raw ticket row appeared.
2. With opt-in on and Auto-Convert off, two different tickets imported distinct PDFs as books 224 and 225. Their title fallback retained their basenames.
3. Redropping identical ticket bytes acknowledged the durable source receipt, with no additional hook or duplicate book.
4. A deliberate plugin rejection reached the original backup and created no raw ACSM format.
5. Auto-Convert on imported the fulfilled PDF as a real EPUB (book 226), through actual ebook-convert.
6. The actual Calibre transaction's precommit fault gate rolled back an import after fulfillment. The original and validated PDF remained. After replacing the owned container with the current-main image and preserving its volumes, a fresh process imported the same PDF as book 227. The diagnostic hook executed once for that ticket; the cache disappeared only after commit.

Image `sha256:4f518367d4b543b8164bbba13369d80e0e65ef4b99a311b7da0847424dda9083` booted once. Embedded fulfillment, ingest, guarded-transaction, and plugin-environment files match frozen source `d53ecd7286e3015472f20beee0744645a1835233` by SHA-256. The private evidence packet retains the exact commands, source manifests, watcher logs, transaction fault/restart results, and compressed UI captures.

The New UI and Classic UI showed the imported books at desktop and phone widths. Four actual downloads covered the two fulfilled PDFs, the auto-converted EPUB, and the PDF recovered after restart. The first imported PDF download was parsed by the running Calibre metadata reader; its expected ticket-derived title was preserved. Library bytes matched the recovery manifest before download. Download bytes may differ because the existing metadata-embedding option exports an updated copy; this is not a claim of byte-identical exported PDFs. Compressed captures preserve both interfaces and viewports.

The final current-main focused packet passed 106 tests; an independent packet passed 114. The full correct non-integration lane passed 10,332, skipped 123, and reproduced only the two known clean-main Mac SQLite replacement/I/O failures. The serial integration lane passed one test. Linux CI remains the required gate for the proposed PR head.

## Limits

Genuine Adobe fulfillment is unobserved: no authorized Adobe configuration and real ticket were supplied. The tested framework uses Calibre's actual import-hook and transaction runtime with owned diagnostic books; this is not evidence of an Adobe authorization, DRM-removal, or KFX success. The result validator checks EPUB packaging or PDF header/trailer structure, not every document's rendering or security. An interrupted third-party hook before any book can be copied cannot guarantee retrieval of its remote download; the retained reservation prevents automatic repeat fulfillment and identifies the recovery directory. Physical devices and Windows process-tree behavior were not tested.


## Serial current-main integration — 2026-10-02

Rebased onto `ede9d1ae35bf1447e6bd0802bfc53333bb36b3fc`, including the installed conversion-capability registry and all preceding reader, selection, Discover, ingest-folder-label and file-metadata work. Runtime rebase head `ba43924a657adf78ba4116d5e8150c96edeae856` retains the original fulfillment helper and four behavioral feature test files byte-for-byte. The README conflict preserves the full current registry paragraph and adds both ACSM fulfillment and durable-recovery paragraphs; all main changelog rows, SPA anchors and locale catalogs survive. The existing measured classifier remains225/284.

Current focused ACSM/failed-conversion/folder-label/plugin/classifier packet:216passed38.67s. Independent bounded source integration is CLEAR: the original ticket path is restored before guarded import, separate staged package/source identities and current receipt ownership remain intact, and folder-label replay uses the existing guarded narrow transaction. This is source integration evidence; the genuine Adobe limitation and historical full/native proofs above remain separate. New complete-image behavior, including labels derived from the original watched ticket after conversion and on receipt replay, exact resulting-head CI and fresh main/head/persona gates remain required for the serial merge. No release, deployment, original-issue mutation or comment.
