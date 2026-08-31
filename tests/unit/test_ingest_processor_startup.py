import os
import subprocess
import sys
import textwrap
import importlib
import inspect
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def test_import_and_missing_target_skip_heavy_startup(tmp_path):
    config_processed_books = Path("/config/processed_books")
    config_processed_books_existed = config_processed_books.exists()
    script = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path

        scripts_dir = Path(os.environ["CWA_TEST_SCRIPTS_DIR"])
        tmp_path = Path(os.environ["CWA_TEST_TMPDIR"])
        sys.path.insert(0, str(scripts_dir))

        import ingest_processor

        lock_path = tmp_path / "ingest_processor.lock"
        if lock_path.exists():
            raise AssertionError("default lock was created during import")
        if ingest_processor.process_lock is not None:
            raise AssertionError("default process lock was initialized during import")

        result = ingest_processor.main(str(tmp_path / "missing.epub"))
        if result not in (0, None):
            raise AssertionError(f"missing target returned unexpected status: {result!r}")
        if lock_path.exists():
            raise AssertionError("default lock was created for missing target")
        if ingest_processor.process_lock is not None:
            raise AssertionError("process lock was initialized for missing target")
        """
    )
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    env["CWA_TEST_TMPDIR"] = str(tmp_path)
    env["CWA_TEST_SCRIPTS_DIR"] = str(Path(__file__).resolve().parents[2] / "scripts")

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[ingest-processor] Skipping missing ingest target:" in result.stdout
    assert "File did not become ready in time or vanished" not in result.stdout
    assert not (tmp_path / "ingest_processor.lock").exists()
    if not config_processed_books_existed:
        assert not config_processed_books.exists()


def test_failed_runtime_initialization_is_not_retried(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    ingest_processor = importlib.import_module("ingest_processor")
    ingest_processor._runtime_initialized = False
    ingest_processor._runtime_init_attempted = False
    ingest_processor.process_lock = None

    acquire_calls = 0

    class ContendedLock:
        def acquire(self, timeout=5):
            nonlocal acquire_calls
            acquire_calls += 1
            assert timeout == 10
            return False

        def release(self):
            pass

    monkeypatch.setattr(ingest_processor, "_ensure_project_root_on_path", lambda: None)
    monkeypatch.setattr(ingest_processor, "_load_runtime_dependencies", lambda: None)
    monkeypatch.setattr(ingest_processor, "_load_optional_cps_modules", lambda: None)
    monkeypatch.setattr(ingest_processor, "ProcessLock", ContendedLock)

    assert ingest_processor.initialize_runtime() is False
    assert ingest_processor.initialize_runtime() is False
    assert acquire_calls == 1


def _process_lock_class(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    ingest_processor = importlib.import_module("ingest_processor")
    monkeypatch.setattr(
        ingest_processor.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )
    return ingest_processor.ProcessLock


def test_process_lock_live_holder_cannot_be_stolen(monkeypatch, tmp_path):
    process_lock_class = _process_lock_class(monkeypatch, tmp_path)
    lock_path = tmp_path / "live-holder.lock"
    holder = process_lock_class("live-holder")
    contenders = []

    try:
        assert holder.acquire(timeout=0.1)

        contender = process_lock_class("live-holder")
        contenders.append(contender)
        assert contender.acquire(timeout=0.1) is False
        assert lock_path.exists(), "live holder's lock file was unlinked by contender"
        assert lock_path.read_text() == str(os.getpid())

        second_contender = process_lock_class("live-holder")
        contenders.append(second_contender)
        assert second_contender.acquire(timeout=0.1) is False
    finally:
        for contender in contenders:
            contender.release()
        holder.release()


def test_process_lock_invalid_pid_cannot_override_live_flock(monkeypatch, tmp_path):
    process_lock_class = _process_lock_class(monkeypatch, tmp_path)
    lock_path = tmp_path / "invalid-pid.lock"
    holder = process_lock_class("invalid-pid")
    contender = process_lock_class("invalid-pid")

    try:
        assert holder.acquire(timeout=0.1)
        lock_path.write_text("not-a-pid")

        assert contender.acquire(timeout=0.1) is False
        assert lock_path.exists()
        assert lock_path.read_text() == "not-a-pid"
    finally:
        contender.release()
        holder.release()


def test_process_lock_reclaims_dead_pid_file(monkeypatch, tmp_path):
    process_lock_class = _process_lock_class(monkeypatch, tmp_path)
    lock_path = tmp_path / "stale-holder.lock"
    exited_process = subprocess.Popen([sys.executable, "-c", "pass"])
    exited_process.wait(timeout=5)
    dead_pid = exited_process.pid
    lock_path.write_text(str(dead_pid))
    lock = process_lock_class("stale-holder")

    try:
        with pytest.raises(ProcessLookupError):
            os.kill(dead_pid, 0)
        assert lock.acquire(timeout=0.1)
        assert lock_path.read_text() == str(os.getpid())
    finally:
        lock.release()


def test_process_lock_read_only_file_remains_lockable(monkeypatch, tmp_path):
    process_lock_class = _process_lock_class(monkeypatch, tmp_path)
    lock_path = tmp_path / "read-only.lock"
    original_contents = "previous-holder"
    lock_path.write_text(original_contents)
    lock_path.chmod(0o444)
    holder = process_lock_class("read-only")
    contender = process_lock_class("read-only")

    try:
        assert holder.acquire(timeout=0.1)
        assert lock_path.exists()
        assert lock_path.read_text() == original_contents

        assert contender.acquire(timeout=0.1) is False
        assert lock_path.exists()
        assert lock_path.read_text() == original_contents
    finally:
        contender.release()
        holder.release()

    assert lock_path.exists()
    assert lock_path.read_text() == original_contents


def test_optional_cps_modules_retry_after_partial_load(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    ingest_processor = importlib.import_module("ingest_processor")
    source = inspect.getsource(ingest_processor._load_optional_cps_modules)

    assert "if _GDRIVE_AVAILABLE and _CPS_AVAILABLE:" in source
    assert "if _GDRIVE_AVAILABLE or _CPS_AVAILABLE:" not in source
