# SPDX-License-Identifier: GPL-3.0-or-later
"""A transient library owner must leave the published ingest source retryable."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def ingest(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import ingest_processor
    import calibre_library_target

    monkeypatch.setattr(calibre_library_target, "config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(ingest_processor, "_acquire_process_lock_or_exit", lambda: None)
    monkeypatch.setattr(ingest_processor, "initialize_runtime", lambda: True)
    monkeypatch.setattr(ingest_processor, "is_a_book_format", lambda _format: True)
    monkeypatch.setattr(ingest_processor, "mark_ingest_batch_active", lambda: None)
    monkeypatch.setattr(ingest_processor, "clear_ingest_batch_active", lambda: None)
    monkeypatch.setattr(ingest_processor, "wait_for_duplicate_full_scan_to_finish", lambda: None)
    return ingest_processor, calibre_library_target


def processor(module, source, tmp_path):
    p = object.__new__(module.NewBookProcessor)
    p.filepath = str(source)
    p.filename = source.name
    p.input_format = "epub"
    p.is_target_format = True
    p.ingest_ignored_formats = []
    p.cwa_settings = {"ingest_timeout_minutes": 15}
    p.library_dir = str(tmp_path / "library")
    p.staging_dir = str(tmp_path / "staging")
    p.tmp_conversion_dir = str(tmp_path / "conversion")
    Path(p.staging_dir).mkdir()
    p.calibre_env = {}
    p.is_file_in_use = lambda: True
    p.set_library_permissions = lambda: None
    p.delete_current_file = lambda: source.unlink(missing_ok=True)
    p._validate_book_exists = lambda _book: True
    return p


def test_default_off_ingest_keeps_source_while_convert_library_owns_maintenance(
    ingest, monkeypatch, tmp_path
):
    module, routing = ingest
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"published source")
    p = processor(module, source, tmp_path)
    monkeypatch.setattr(module, "NewBookProcessor", lambda _path: p)
    with routing.ownership.maintenance(str(tmp_path)):
        try:
            result = module.main(str(source))
        except RuntimeError:
            result = "unexpected exception"
    assert source.is_file(), "main deleted the source after a busy maintenance owner"
    assert source.read_bytes() == b"published source"
    assert result == 2, "busy input must enter the service's existing retry queue"


@pytest.mark.parametrize("failure", [TimeoutError("writer deadline"), "maintenance busy"])
def test_add_format_coordination_failure_keeps_source_and_manifest(
    ingest, monkeypatch, tmp_path, failure
):
    module, _routing = ingest
    if failure == "maintenance busy":
        failure = module.LibraryBusyError("maintenance owner")
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"new format source")
    manifest = source.with_name(source.name + ".cwa.json")
    manifest.write_text(json.dumps({"action": "add_format", "book_id": 1}))
    p = processor(module, source, tmp_path)
    monkeypatch.setattr(module, "NewBookProcessor", lambda _path: p)

    @contextmanager
    def blocked():
        raise failure
        yield

    monkeypatch.setattr(module, "operation", blocked)
    result = module.main(str(source))
    assert source.is_file(), "unacknowledged format source was deleted"
    assert source.read_bytes() == b"new format source"
    assert manifest.is_file(), "the add_format intent must remain paired with the source"
    assert result == 2, "transient library ownership must remain eligible for periodic retry"


def test_failed_add_format_command_is_not_acknowledged_or_deleted(ingest, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    module, _routing = ingest
    source = tmp_path / 'incoming.epub'
    source.write_bytes(b'new format source')
    manifest = source.with_name(source.name + '.cwa.json')
    manifest.write_text(json.dumps({'action': 'add_format', 'book_id': 1}))
    p = processor(module, source, tmp_path)
    p.backup = lambda *_args, **_kwargs: None
    monkeypatch.setattr(module, 'NewBookProcessor', lambda _path: p)
    monkeypatch.setattr(module, 'operation', nullcontext)
    monkeypatch.setattr(module, 'library_target', lambda _library: SimpleNamespace(args=[], stdin=None))
    def failed(*_args, **_kwargs):
        raise module.subprocess.CalledProcessError(1, ['calibredb', 'add_format'], stderr='library busy')
    monkeypatch.setattr(module.subprocess, 'run', failed)
    result = module.main(str(source))
    assert source.is_file(), 'failed Calibre command must retain the published source'
    assert manifest.is_file(), 'failed command cannot acknowledge its sidecar intent'
    assert result == 1


@pytest.mark.parametrize("busy", [False, True])
def test_retained_original_format_failure_keeps_source_for_idempotent_retry(
        ingest, monkeypatch, tmp_path, busy):
    """A committed conversion does not acknowledge its uncommitted original format."""
    module, _routing = ingest
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"original format to retain")
    converted = tmp_path / "converted.epub"
    converted.write_bytes(b"converted package")
    p = processor(module, source, tmp_path)
    p.input_format = "txt"
    p.is_target_format = False
    p.is_supported_audiobook = lambda: False
    p.can_convert = True
    p.auto_convert_on = True
    p.convert_ignored_formats = []
    p.convert_retained_formats = ["txt"]
    p.target_format = "epub"
    p.convert_book = lambda: (True, str(converted))
    p.add_book_to_library = lambda *_args, **_kwargs: None
    p.last_added_book_id = 7

    def busy_original(*_args):
        error = module.LibraryBusyError if busy else module.RetryIngestSourceError
        raise error("unready library owner")

    p.add_format_to_book = busy_original
    monkeypatch.setattr(module, "NewBookProcessor", lambda _path: p)
    result = module.main(str(source))
    assert source.is_file(), "converted book cleanup deleted its uncommitted retained format"
    assert result == (2 if busy else 1), "retained-format failure lost its retry class"


def test_busy_maintenance_is_checked_before_conversion(ingest, monkeypatch, tmp_path):
    """A queued book waiting for Convert Library must not repeatedly run ebook-convert."""
    module, routing = ingest
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"source waiting on maintenance")
    p = processor(module, source, tmp_path)
    p.input_format = "txt"
    p.is_target_format = False
    p.is_supported_audiobook = lambda: False
    p.can_convert = True
    p.auto_convert_on = True
    p.convert_ignored_formats = []
    p.target_format = "epub"
    converted = []
    p.convert_book = lambda: (converted.append(True) or False, "")
    p.add_book_to_library = lambda *_args, **_kwargs: None
    monkeypatch.setattr(module, "NewBookProcessor", lambda _path: p)
    with routing.ownership.maintenance(str(tmp_path)):
        result = module.main(str(source))
    assert converted == [], "busy maintenance still allowed an expensive conversion"
    assert result == 2 and source.is_file()


def test_busy_maintenance_skips_expensive_runtime_initialization(ingest, monkeypatch, tmp_path):
    module, routing = ingest
    source = tmp_path / "waiting.epub"
    source.write_bytes(b"source waiting for conversion")
    monkeypatch.setattr(module, "initialize_runtime", lambda: pytest.fail("busy retry loaded conversion runtime"))
    with routing.ownership.maintenance(str(tmp_path)):
        assert module.main(str(source)) == 2
    assert source.read_bytes() == b"source waiting for conversion"
