# Kobo device harness contract

The Kobo device harness is the hardware verification surface for Kobo-native
paths. It drives one explicitly configured probe reader through stock SSH,
framebuffer capture, and synthetic touch primitives; pulls the reader database
read-only; records probe-scoped server state; and emits a structured verdict.
It is not part of the application repository. It lives in the untracked
project-root tool directory because that directory contains private device
identity and transport material.

No device MAC, serial, account id, probe book id, private hostname, address, or
credential belongs in this document, a commit, a pull request, or an issue.
Those values live only in the ignored, mode-0600 harness config and private run
artifacts.

## Invocation and verdict contract

From the project-root tool directory:

```bash
./kobo-pilot scenario <name> --dry-run
./kobo-pilot scenario <name>
./kobo-pilot scenario cleanup
```

`--dry-run` validates the config and prints the manager plan without contacting
the device or server. Its verdict is `NOT_RUN`; it is not verification.

A hardware run refuses before observation unless all of these guards pass:

1. Exact-MAC discovery resolves the configured reader without a subnet scan.
2. The serial and model read from the resolved reader's version record match
   the configured Clara.
3. Every server read/write predicate is derived from the configured probe user
   and configured probe books.
4. The device database is copied off the reader with its WAL/SHM companions and
   opened locally with SQLite `mode=ro` plus `PRAGMA query_only=ON`.

Every real run creates `result.json` and `control-diff.json` in a private run
directory. The common evidence chain is:

```text
identity preflight
  → CONTROL SNAPSHOT before
  → setup / trigger / measured intermediate snapshots and captures
  → CONTROL SNAPSHOT after
  → structured diff
  → mutation restoration report
  → verdict
```

The control snapshot records each configured book's `content` row keyed by
ContentID with `IsDownloaded`, `___SyncTime`, `___FileSize`,
`___PercentRead`, and `ChapterIDBookmarked`. It records every probe-book
`Bookmark` row keyed by BookmarkID with `Hidden`, `SyncTime`, `Type`,
`VolumeID`, and the content/text/note clocks needed to explain a change. The
server half records the probe user's synced-book, reading-state, annotation,
annotation-authority/ETag, and book-metadata rows.

Evidence links are individually labelled `OBSERVED` or `ASSUMED`. A screenshot
proves pixels/text observed in that capture; it does not prove a database or
wire effect. A sleep proves elapsed time only. Those effects require a DB
snapshot, capture filename, or read-only server query output. “Nothing
happened” is never accepted without a positive control when the negative is
the result under test.

Verdicts are:

- `PASS`: every required measured predicate passed.
- `FAIL`: the run completed and a required predicate did not pass.
- `ASSUMED`: the run reached a boundary the shipped product cannot yet deliver
  or no valid oracle was configured. This is intentionally not a pass.
- `ERROR`: guards, execution, evidence capture, or restoration failed before a
  valid product verdict.
- `NOT_RUN`: plan only; no hardware claim.

## Server mutation boundary

All direct server database reads execute through SQLite read-only URI mode in
the configured application container. There is no arbitrary server SQL command
in the scenario interface.

Exactly three direct writes exist:

1. bump `books.last_modified` for one configured probe book;
2. bump `kobo_reading_state.last_modified` for the configured probe user/book;
   and
3. clear `KoboSyncedBooks` for the configured probe user to force replay.

Each write records the old value/rows before mutation, writes an on-disk
recovery journal, and restores in `finally`. `scenario cleanup` also scans
sibling private run directories for an unfinished journal left by a killed
process and restores it only when its complete user/book scope matches the
current config. A scope mismatch refuses rather than guessing.

Web-reader rehydration setup is separate from those three DB writes. It uses
the authenticated SPA API after `/auth/me` proves the session is the configured
probe account. Scenario-created web annotations get private cleanup records;
`scenario cleanup` deletes them through the same API.

## Scenario registry

Hardware-run status is `not yet` until a real run's `result.json` is cited.

| Scenario | What it proves | Positive control | Hardware run |
|---|---|---|---|
| `sync-normal` | Routine sync does not de-download configured probe books. | OCR-located Sync now activation bracketed by device/server snapshots. | not yet |
| `sync-replay-token-loss` | Full replay after probe-user synced-token loss does not de-download books. | A probe-book metadata bump must first cause a detected downloaded→not-downloaded flip. | not yet |
| `dedownload-detect` | The harness detects the per-book flip itself. | Whitelisted metadata bump for the configured probe book. | not yet |
| `position-order-download-then-sync` | Position rehydrates when download precedes reading-state sync. | The same run first proves de-download detection. | not yet |
| `position-order-sync-then-download` | Position rehydrates when reading-state sync precedes download. | The same run first proves de-download detection. | not yet |
| `rehydrate-position` | A server-held position reaches the reader. | Probe-user/book reading-state timestamp bump. | not yet |
| `rehydrate-highlights` | A highlight created by the web-reader device reaches Kobo. | Successful SPA create plus owned-book exchange capture. | not yet |
| `rehydrate-notes` | A note created by the web-reader device reaches Kobo. | Successful SPA create plus owned-book exchange capture. | not yet |
| `rehydrate-dogears` | A browser-created dogear reaches Kobo when the product supports that class. | The SPA/API half is measured; absent shipped delivery is `ASSUMED`, never passed. | not yet |
| `deletion-uploads-on-close` | Corner-toggle deletion uploads on close while panel deletion waits for library sync. | Each phase first creates and observes a new BookmarkID. | not yet |
| `tombstone-survives-reboot` | A Hidden deletion row survives reboot. | A new BookmarkID is created before deletion; reboot refuses if firmware is staged. | not yet |
| `delete-on-the-wire` | The exact deleted BookmarkID appears in `deletedAnnotationIds`. | The BookmarkID is created and observed before deletion. | not yet |
| `annotation-ownership-etag` | Owned GET is local, CWNG ETag is adopted/echoed, and the owned response is never 304. | Multiple open/close/sync exchanges are captured and correlated. | not yet |
| `backup-readonly-restore` | An idle backup can be copied and integrity-checked locally without writing the reader. | SHA-256 equality and local `integrity_check`. | not yet |
| `capture-store-assert` | Capture records have the documented schema/redaction and no upstream leg for owned traffic. | A fresh owned open/close exchange is generated before inspection. | not yet |
| `firmware-install-via-reboot` | A staged firmware payload installs specifically through reboot. | Staged payload plus before/after version reads and Home captures. | not yet |
| `cleanup` | Probe mutations/artifacts are restored and configured books are downloaded again. | Final control snapshot after cleanup sync. | not yet |

## Manager run procedure

Before a run, the manager should:

1. choose the scenario and read its dry-run plan;
2. confirm the Clara is awake on Home, idle, charged, and not USB-mounted or
   already syncing;
3. confirm the configured probe account/book fixtures are disposable and the
   expected-position/web-payload or UI-action fixture needed by that scenario
   is present;
4. enable private exchange capture only for capture/wire scenarios, then
   disable it immediately afterwards; and
5. inspect Home for an update modal. Only the dedicated firmware scenario may
   reboot with a staged update. The tombstone reboot scenario refuses it.

After the command, the manager reads `result.json` first, then follows every
evidence path named by the failed/assumed predicate. It must run `cleanup`
after a failed or interrupted mutating scenario and confirm its final snapshot.
The manager may call a scenario hardware-verified only when every load-bearing
link is `OBSERVED`; any `ASSUMED` link must be named in the report.

## Pull-request gate

Every pull request that changes a Kobo-native path must cite the relevant named
scenario run. The citation should include:

- scenario name;
- verdict;
- firmware label;
- private run timestamp or operator-local artifact identifier (never private
  paths, device/account identifiers, capture bodies, or credentials); and
- every remaining `ASSUMED` link.

Unit/integration tests remain required. A fake scenario run proves harness
logic, not Nickel behavior. Conversely, an uncaptured manual device action is
not a repeatable scenario run and does not satisfy this gate.
