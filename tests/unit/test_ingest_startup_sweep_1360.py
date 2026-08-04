# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork #1360 — books already in the ingest folder when
the container starts are never imported.

``cwa-ingest-service`` is purely event-driven on the inotify path: it ends in
a bare ``inotifywait -m`` pipeline, and inotify only reports events that occur
*after* its watches are established. There is no initial scan, so any file
already sitting in the watch folder at boot is never seen — no error, no log
line, the book simply never appears in the library. Restarting does not help,
because the next boot has the same blind spot. The user has to touch every
file again to generate a fresh event.

Reproduced 2026-08-04 against the real run script in a Linux container, with
only the Docker-Desktop detection neutralized so the inotify branch is taken
(the branch a default native-Linux Docker install gets):

    A) file present before the watcher boots -> 0 processor invocations
    B) control, file created after boot      -> 1 processor invocation

The watcher itself is fine; only pre-existing files are dropped.

Why this stayed quiet: the polling fallback does **not** have the gap.
``watch_fallback.py`` walks the tree with ``os.walk`` on its first pass, so
pre-existing files are emitted there. Every setup that takes the fallback —
``NETWORK_SHARE_MODE``, ``CWA_WATCH_MODE=poll``, and Docker Desktop — is
therefore immune, which is most of the environments this gets tested in. The
default native-Linux inotify path is the one that bites, and it is the most
common production deployment.

The fix adds a bounded startup sweep over the watch folder before the inotify
loop, and drains the retry queue at boot (its file is documented as
"persistent across restarts", but nothing ever read it back — ``process_retry_queue``
was only ever called from inside ``handle_event``).
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = (
    REPO_ROOT / "root" / "etc" / "s6-overlay" / "s6-rc.d" / "cwa-ingest-service" / "run"
)


@pytest.fixture
def harness(tmp_path):
    """Source the run script in TEST_MODE with a processor stub, then run an
    arbitrary snippet of bash against the sourced functions."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script missing")

    watch = tmp_path / "watch"
    processing = tmp_path / "processing"
    recent = tmp_path / "recent"
    for d in (watch, processing, recent):
        d.mkdir()

    processor_log = tmp_path / "processor.log"
    processor_log.write_text("")
    retry_queue = tmp_path / "retry_queue"

    stub = tmp_path / "processor-stub.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$1" >> "$PROCESSOR_LOG"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)

    post_batch_stub = tmp_path / "post-batch-stub.sh"
    post_batch_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_batch_stub.chmod(0o755)

    env = {
        **os.environ,
        "WATCH_FOLDER": str(watch),
        "CWA_INGEST_SERVICE_TEST_MODE": "1",
        "CWA_INGEST_PROCESSING_DIR": str(processing),
        "CWA_INGEST_RECENT_DIR": str(recent),
        "CWA_INGEST_RETRY_QUEUE": str(retry_queue),
        "CWA_INGEST_STATUS_FILE": str(tmp_path / "status"),
        "CWA_INGEST_RECENT_EVENT_TTL": "120",
        "CWA_INGEST_BATCH_DIRTY_FILE": str(tmp_path / "batch_dirty"),
        "CWA_INGEST_BATCH_LAST_SUCCESS_FILE": str(tmp_path / "batch_success"),
        "CWA_INGEST_BATCH_QUIET_SECONDS": "1",
        "CWA_INGEST_POST_BATCH_CMD": str(post_batch_stub),
        "CWA_INGEST_PROCESSOR_CMD": str(stub),
        "PROCESSOR_LOG": str(processor_log),
        # Keep the stability probe fast for tests.
        "CWA_INGEST_STABLE_CHECKS": "2",
        "CWA_INGEST_STABLE_CONSEC_MATCH": "2",
        "CWA_INGEST_STABLE_INTERVAL": "0.05",
    }

    def run(snippet: str):
        script = textwrap.dedent(
            f"""
            set -uo pipefail
            source "{RUN_SCRIPT}" >/dev/null 2>&1
            {snippet}
            """
        )
        subprocess.run(["bash", "-c", script], env=env, check=False)
        return processor_log.read_text().splitlines()

    run.watch = watch
    run.retry_queue = retry_queue
    run.processor_log = processor_log
    return run


# --------------------------------------------------------------------------
# Behavioural pins — the user-visible symptom
# --------------------------------------------------------------------------

def test_startup_sweep_ingests_preexisting_file(harness):
    """THE reported symptom: a book already in the ingest folder when the
    service starts must be imported.

    RED on main — no startup sweep exists, so nothing ever processes it."""
    book = harness.watch / "already-here.epub"
    book.write_text("a whole book")

    processed = harness("startup_ingest_sweep >/dev/null 2>&1 || true")

    assert str(book) in processed, (
        "a book present in the ingest folder at service start was never "
        "ingested (#1360) — inotify has no initial scan, so without a "
        "startup sweep it stays unimported forever"
    )


def test_startup_sweep_recurses_into_subfolders(harness):
    """The watcher is recursive (`inotifywait -r`) and download clients drop
    books into per-author subfolders, so the sweep must recurse too."""
    sub = harness.watch / "Author Name" / "Series"
    sub.mkdir(parents=True)
    book = sub / "nested.epub"
    book.write_text("a nested book")

    processed = harness("startup_ingest_sweep >/dev/null 2>&1 || true")

    assert str(book) in processed, (
        "startup sweep must recurse into subfolders — the inotify watch does"
    )


def test_startup_sweep_skips_unsupported_and_partial_files(harness):
    """The sweep must apply the same filters as the event path: no temp
    suffixes (a download interrupted by the restart), no sidecar manifests,
    no unsupported extensions. Ingesting a `.crdownload` would import a
    half-downloaded book."""
    partial = harness.watch / "still-downloading.epub.crdownload"
    partial.write_text("half a book")
    sidecar = harness.watch / "book.cwa.json"
    sidecar.write_text("{}")
    unsupported = harness.watch / "cover.jpg"
    unsupported.write_text("not a book")
    real = harness.watch / "real.epub"
    real.write_text("a whole book")

    processed = harness("startup_ingest_sweep >/dev/null 2>&1 || true")

    assert str(real) in processed
    for skipped in (partial, sidecar, unsupported):
        assert str(skipped) not in processed, (
            f"startup sweep must not ingest {skipped.name}"
        )


def test_startup_sweep_is_noop_on_empty_folder(harness):
    """An empty ingest folder is the overwhelmingly common case; the sweep
    must not invoke the processor at all (and must not fail)."""
    processed = harness("startup_ingest_sweep >/dev/null 2>&1 || true")
    assert processed == [], (
        f"startup sweep touched the processor on an empty folder: {processed}"
    )


def test_retry_queue_is_drained_at_startup(harness):
    """The retry queue file is documented as "persistent across restarts", but
    nothing ever read it back: `process_retry_queue` was only reachable from
    inside `handle_event`, so a queued file was stranded until some *other*
    file happened to arrive.

    RED on main via the boot wiring pin below; this pins the behaviour that
    draining the queue actually re-runs the processor."""
    queued = harness.watch / "queued-last-run.epub"
    queued.write_text("a whole book")
    harness.retry_queue.write_text(f"{queued}\n")

    processed = harness("process_retry_queue >/dev/null 2>&1 || true")

    assert str(queued) in processed, (
        "a file left in the persistent retry queue was not retried"
    )


# --------------------------------------------------------------------------
# Wiring pins — a sweep that is defined but never called is a no-op
# --------------------------------------------------------------------------

def _boot_section() -> str:
    """The part of the script that runs after the TEST_MODE early-return —
    i.e. the actual service boot sequence."""
    text = RUN_SCRIPT.read_text()
    marker = 'CWA_INGEST_SERVICE_TEST_MODE'
    idx = text.index(marker)
    return text[idx:]


def test_boot_sequence_calls_startup_sweep():
    """Defining the function is not enough — the boot path must invoke it,
    or #1360 is unfixed while the unit tests go green."""
    boot = _boot_section()
    assert "startup_ingest_sweep" in boot, (
        "startup_ingest_sweep is never called from the service boot sequence; "
        "a sweep that no one runs does not fix #1360"
    )


def test_boot_sequence_drains_retry_queue():
    """Same for the persistent retry queue — it must be drained at boot."""
    boot = _boot_section()
    assert "process_retry_queue" in boot, (
        "the persistent retry queue is never drained at startup; files queued "
        "before a restart stay stranded until an unrelated file arrives"
    )


def test_startup_sweep_defined_before_test_mode_guard():
    """The sweep must be defined above the TEST_MODE early-return so it is
    available to sourced-script tests (and so the boot section can call it)."""
    text = RUN_SCRIPT.read_text()
    assert "startup_ingest_sweep()" in text, "startup_ingest_sweep is not defined"
    assert text.index("startup_ingest_sweep()") < text.index(
        "CWA_INGEST_SERVICE_TEST_MODE"
    ), "startup_ingest_sweep must be defined before the TEST_MODE guard"
