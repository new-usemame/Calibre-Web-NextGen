"""ACSM tickets need FileTypePlugin import hooks, never ebook-convert input.

The main-flow seam is checked with the old converter deliberately failing. A
successful import hook must still pass the resulting book to guarded ingest.
"""
import pytest
from tests.unit.test_984_ticket_fulfilment_without_auto_convert import _run_main

pytestmark = pytest.mark.unit

@pytest.mark.parametrize("auto_convert_on,can_convert,ignored", [
    (False, True, ()), (True, False, ()), (True, True, ("acsm",)),
])
def test_acsm_uses_import_fulfillment_independent_of_converter_flags(
    monkeypatch, tmp_path, auto_convert_on, can_convert, ignored,
):
    fulfilled = str(tmp_path / "fulfilled.epub")
    fake, source = _run_main(monkeypatch, tmp_path, input_format="acsm",
        auto_convert_on=auto_convert_on, can_convert=can_convert,
        convert_ignored_formats=ignored, convert_result=(True, fulfilled))
    assert fake.fulfillment_calls == 1
    assert fake.convert_book_calls == []
    assert fake.imported == [fulfilled]
    assert source not in fake.imported

from pathlib import Path
import json
import subprocess
import sys
import types
import zipfile
import ingest_processor


def _epub(path):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
        z.writestr('content.opf', '<package/>')
    return path


def _processor(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_processor.app_paths, 'processed_books_dir', lambda: tmp_path / 'processed_books')
    processor = ingest_processor.NewBookProcessor.__new__(ingest_processor.NewBookProcessor)
    processor.filepath = str(tmp_path / 'ticket.acsm')
    Path(processor.filepath).write_text('owned-ticket')
    processor.filename = 'ticket.acsm'
    processor.input_format = 'acsm'
    processor.target_format = 'epub'
    processor.tmp_conversion_dir = str(tmp_path / 'conversion')
    Path(processor.tmp_conversion_dir).mkdir()
    processor.auto_convert_on = False
    processor.convert_ignored_formats = []
    processor.calibre_env = {'OWNED_PLUGIN_ENV': 'same-job-environment'}
    processor.last_added_book_ids = []
    processor.imported = []
    processor.backed_up = []
    monkeypatch.setattr(processor, '_content_marker_book_ids', lambda digest: [])
    def imported(path, **kw):
        processor.imported.append((path, kw))
        processor.last_added_book_ids = [7]
    monkeypatch.setattr(processor, 'add_book_to_library', imported)
    monkeypatch.setattr(processor, 'backup', lambda path, backup_type: processor.backed_up.append((path, backup_type)) or True)
    monkeypatch.setattr(ingest_processor, 'conversion_budget_remaining', lambda: 5)
    return processor


def _acsm_import_process_probe(tmp_path, failure_name):
    """Drive real main/fulfillment/recovery; only the external hook/import are seams."""
    from calibre_ticket_fulfillment import file_digest, load_result, persist_result

    with pytest.MonkeyPatch.context() as monkeypatch:
        processor = _processor(monkeypatch, tmp_path)
        ticket = Path(processor.filepath)
        manifest = Path(str(ticket) + '.cwa.json')
        manifest.write_text('{"action":"import","original_filename":"owned.acsm"}')
        original = ticket.read_bytes(), manifest.read_bytes()
        processor.ingest_ignored_formats = []
        processor.cwa_settings = {'ingest_timeout_minutes': 1}
        monkeypatch.setattr(processor, 'is_file_in_use', lambda: True)
        monkeypatch.setattr(processor, 'set_library_permissions', lambda: None)
        monkeypatch.setattr(processor, 'delete_current_file', ticket.unlink)
        monkeypatch.setattr(ingest_processor, 'NewBookProcessor', lambda path: processor)
        monkeypatch.setattr(ingest_processor, 'initialize_runtime', lambda: True)
        monkeypatch.setattr(ingest_processor, '_acquire_process_lock_or_exit', lambda: None)
        # Reach contention at import, after fulfillment has durably completed.
        monkeypatch.setattr(ingest_processor, 'check_maintenance', lambda: None)
        hooks = []

        def fulfill(cmd, **kwargs):
            destination = Path(cmd[cmd.index('--destination') + 1])
            destination.mkdir(parents=True)
            book = _epub(destination / 'ticket.epub')
            hooks.append(str(book))
            return 'CWNG_FULFILLMENT_RESULT=' + json.dumps(persist_result(
                destination, file_digest(ticket), book))

        monkeypatch.setattr(ingest_processor, '_run_converter_streaming', fulfill)
        failures = {
            'busy': ingest_processor.LibraryBusyError,
            'timeout': TimeoutError,
            'permission': PermissionError,
            'retry': ingest_processor.RetryIngestSourceError,
            'unexpected': RuntimeError,
            'terminal': ingest_processor.PreserveIngestSourceError,
        }

        def failed_import(path, **kwargs):
            assert kwargs == {'identity_path': str(ticket)}
            assert Path(path).read_bytes().startswith(b'PK')
            raise failures[failure_name]('owned import failure')

        monkeypatch.setattr(processor, 'add_book_to_library', failed_import)
        first_status = ingest_processor.main(str(ticket))
        digest = file_digest(ticket)
        destination = tmp_path / 'processed_books' / 'acsm_fulfilled' / digest
        recovered = load_result(destination, digest)
        fulfilled = Path(recovered['path']).read_bytes()
        retained = ticket.read_bytes(), manifest.read_bytes()
        assert retained == original
        assert not Path(processor.tmp_conversion_dir).exists()

        def completed_import(path, **kwargs):
            assert kwargs == {'identity_path': str(ticket)}
            assert Path(path).read_bytes() == fulfilled
            processor.last_added_book_ids = [7]

        monkeypatch.setattr(processor, 'add_book_to_library', completed_import)
        Path(processor.tmp_conversion_dir).mkdir()
        recovered_status = ingest_processor.main(str(ticket))
        (tmp_path / 'observed.json').write_text(json.dumps({
            'first_status': first_status,
            'recovered_status': recovered_status,
            'fulfillment_calls': len(hooks),
            'ticket_retained_after_failure': retained == original,
            'ticket_removed_after_success': not ticket.exists(),
            'journal_removed_after_success': not destination.exists(),
        }))
        return first_status


@pytest.mark.parametrize('failure_name,expected_status,classification', [
    ('busy', 2, 'BUSY'), ('timeout', 2, 'BUSY'), ('permission', 2, 'BUSY'),
    ('retry', 1, 'RETRY'), ('unexpected', 1, 'RETRY'), ('terminal', 3, 'TERMINAL'),
])
def test_acsm_import_process_status_preserves_recovery_and_busy_retry_signal(
    tmp_path, failure_name, expected_status, classification,
):
    """The service needs exit 2 for timed retries, without spending a ticket twice."""
    repo = Path(__file__).resolve().parents[2]
    worker = (
        'import sys; from pathlib import Path; '
        'sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]) / "scripts")]; '
        'from tests.unit.test_acsm_import_fulfillment import _acsm_import_process_probe; '
        'sys.exit(_acsm_import_process_probe(Path(sys.argv[2]), sys.argv[3]))'
    )
    result = subprocess.run(
        [sys.executable, '-c', worker, str(repo), str(tmp_path), failure_name],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == expected_status, result.stdout + result.stderr
    observed = json.loads((tmp_path / 'observed.json').read_text())
    assert observed == {
        'first_status': expected_status, 'recovered_status': 0,
        'fulfillment_calls': 1, 'ticket_retained_after_failure': True,
        'ticket_removed_after_success': True, 'journal_removed_after_success': True,
    }
    assert f'[ingest-processor] {classification}:' in result.stdout


@pytest.mark.parametrize('auto_convert,target,conversion_success', [
    (False, 'kepub', None), (True, 'kepub', True), (True, 'kepub', False),
])
def test_fulfilled_book_uses_normal_import_identity_and_optional_conversion(
    monkeypatch, tmp_path, auto_convert, target, conversion_success,
):
    processor = _processor(monkeypatch, tmp_path)
    processor.auto_convert_on, processor.target_format = auto_convert, target
    calls = []
    fulfilled = []
    def hook_process(cmd, env, timeout, owned_process_group):
        assert cmd[0] == 'calibre-debug'
        assert '--source' in cmd
        assert env == processor.calibre_env
        assert timeout == 5
        assert owned_process_group is True
        dest = Path(cmd[cmd.index('--destination') + 1])
        dest.mkdir(parents=True, exist_ok=True)
        book = _epub(dest / 'ticket.epub')
        fulfilled.append(str(book))
        from calibre_ticket_fulfillment import persist_result, file_digest
        return 'CWNG_FULFILLMENT_RESULT=' + json.dumps(persist_result(
            book.parent, file_digest(processor.filepath), book))
    monkeypatch.setattr(ingest_processor, '_run_converter_streaming', hook_process)
    def convert():
        calls.append((processor.filepath, processor.input_format))
        return conversion_success, str(tmp_path / 'converted.kepub')
    monkeypatch.setattr(processor, 'convert_to_kepub', convert)
    ticket = processor.filepath
    processor.ingest_acsm()
    expected = str(tmp_path / 'converted.kepub') if conversion_success else fulfilled[0]
    assert processor.imported == [(expected, {'identity_path': ticket})]
    assert calls == ([(fulfilled[0], 'epub')] if auto_convert else [])
    assert (processor.filepath, processor.input_format) == (ticket, 'acsm')
    assert Path(ticket).read_text() == 'owned-ticket'


def test_completed_ticket_receipt_does_not_run_fulfillment_again(monkeypatch, tmp_path):
    processor = _processor(monkeypatch, tmp_path)
    monkeypatch.setattr(processor, '_content_marker_book_ids', lambda digest: [7])
    monkeypatch.setattr(ingest_processor, '_run_converter_streaming', lambda *a, **k: pytest.fail('receipt recovery must not fulfill'))
    processor.ingest_acsm()
    assert processor.imported == [(processor.filepath, {'identity_path': processor.filepath})]


def test_failed_import_hook_repeats_plugin_reason_and_preserves_ticket(monkeypatch, tmp_path, capsys):
    processor = _processor(monkeypatch, tmp_path)
    manifest = Path(processor.filepath + '.cwa.json')
    manifest.write_text('{"action":"import"}')
    plugin_output = 'ACSM Input v0.1: Trying to parse file source.acsm\nACSM Input v0.1: ADE auth is missing or broken\n'
    def fail(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd, output=plugin_output)
    monkeypatch.setattr(ingest_processor, '_run_converter_streaming', fail)
    processor.ingest_acsm()
    assert processor.imported == []
    assert processor.backed_up == [(processor.filepath, 'failed')]
    assert Path(processor.filepath).read_text() == 'owned-ticket'
    assert not manifest.exists()
    output = capsys.readouterr().out
    assert 'ADE auth is missing or broken' in output
    assert 'installed and did run' in output
    assert 'place the ACSM Input plugin zip' not in output


def test_import_plugin_cannot_delete_original_and_temporary_output_is_materialized(monkeypatch, tmp_path):
    import sys
    from contextlib import nullcontext
    import calibre_ticket_fulfillment as helper
    source = tmp_path / 'ticket.acsm'
    source.write_text('owned-ticket')
    def hook(paths):
        staged = Path(paths[0])
        assert staged != source
        book = _epub(staged.with_suffix('.epub'))
        staged.unlink()  # ACSM Input can delete successfully fulfilled tickets.
        return [str(book)]
    monkeypatch.setitem(sys.modules, 'calibre.db.adding', types.SimpleNamespace(
        run_import_plugins=hook, run_import_plugins_before_metadata=lambda p: nullcontext()))
    result = helper.fulfill_ticket(source, tmp_path / 'result')
    assert source.read_text() == 'owned-ticket'
    assert helper.validate_book(result['path']) == 'epub'
    assert Path(result['path']).is_file()


@pytest.mark.parametrize('returned', ['source.acsm', 'invalid.epub', 'empty.pdf'])
def test_import_hook_must_return_a_materialized_book(monkeypatch, tmp_path, returned):
    import sys
    from contextlib import nullcontext
    import calibre_ticket_fulfillment as helper
    source = tmp_path / 'ticket.acsm'
    source.write_text('owned-ticket')
    def hook(paths):
        path = Path(paths[0]).parent / returned
        if returned != 'source.acsm':
            path.write_bytes(b'not an EPUB' if returned.endswith('.epub') else b'')
        return [str(path)]
    monkeypatch.setitem(sys.modules, 'calibre.db.adding', types.SimpleNamespace(
        run_import_plugins=hook, run_import_plugins_before_metadata=lambda p: nullcontext()))
    with pytest.raises(ValueError): helper.fulfill_ticket(source, tmp_path / 'result')
    assert source.read_text() == 'owned-ticket'
    assert not (tmp_path / 'result').exists()


@pytest.mark.skipif(__import__('os').name != 'posix', reason='Owned process-group cleanup is a POSIX behavior')
def test_fulfillment_runner_reaps_inherited_output_descendants_after_leader_exit():
    import sys
    import time
    import threading
    before = set(threading.enumerate())
    child = 'import time; time.sleep(3)'
    leader = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); print("hook-complete")'
    started = time.monotonic()
    output = ingest_processor._run_converter_streaming(
        [sys.executable, '-c', leader], env=None, timeout=1, owned_process_group=True,
    )
    assert 'hook-complete' in output
    assert time.monotonic() - started < 2, 'returned hook must not retain the output pipe for a sleeping helper'
    assert set(threading.enumerate()) == before


def test_unexpected_acsm_error_never_deletes_the_original_in_main_cleanup(monkeypatch, tmp_path):
    from tests.unit.test_984_ticket_fulfilment_without_auto_convert import _FakeProcessor
    ticket = tmp_path / 'original.acsm'
    ticket.write_text('owned-ticket')
    processor = _FakeProcessor(str(ticket), input_format='acsm', auto_convert_on=False,
                               convert_result=(False, ''))
    def fail(): raise OSError('owned unexpected hook failure')
    processor.ingest_acsm = fail
    processor.delete_current_file = lambda: ticket.unlink()
    monkeypatch.setattr(ingest_processor, 'NewBookProcessor', lambda p: processor)
    monkeypatch.setattr(ingest_processor, 'initialize_runtime', lambda: True)
    monkeypatch.setattr(ingest_processor, '_acquire_process_lock_or_exit', lambda: None)
    assert ingest_processor.main(str(ticket)) == 1
    assert ticket.read_text() == 'owned-ticket'


def test_failed_backup_preserves_original_for_manual_recovery(monkeypatch, tmp_path):
    processor = _processor(monkeypatch, tmp_path)
    def fail(cmd, **kw): raise subprocess.CalledProcessError(1, cmd, output='no import plugin')
    monkeypatch.setattr(ingest_processor, '_run_converter_streaming', fail)
    monkeypatch.setattr(processor, 'backup', lambda *a, **kw: False)
    with pytest.raises(ingest_processor.PreserveIngestSourceError): processor.ingest_acsm()
    assert Path(processor.filepath).read_text() == 'owned-ticket'


def test_import_retry_reuses_fulfilled_bytes_after_conversion_cleanup(monkeypatch, tmp_path):
    import shutil
    processor = _processor(monkeypatch, tmp_path)
    hooks = []
    def fulfill(cmd, **kw):
        hooks.append(cmd)
        assert len(hooks) == 1, 'a retry must not spend the ticket again'
        destination = Path(cmd[cmd.index('--destination') + 1])
        destination.mkdir(parents=True, exist_ok=True)
        book = _epub(destination / 'ticket.epub')
        from calibre_ticket_fulfillment import persist_result, file_digest
        return 'CWNG_FULFILLMENT_RESULT=' + json.dumps(persist_result(
            book.parent, file_digest(processor.filepath), book))
    monkeypatch.setattr(ingest_processor, '_run_converter_streaming', fulfill)
    def fail_import(*a, **kw):
        raise ingest_processor.RetryIngestSourceError('precommit import failure')
    monkeypatch.setattr(processor, 'add_book_to_library', fail_import)
    with pytest.raises(ingest_processor.RetryIngestSourceError): processor.ingest_acsm()
    shutil.rmtree(processor.tmp_conversion_dir)
    Path(processor.tmp_conversion_dir).mkdir()
    def recovered(path, **kw):
        assert Path(path).read_bytes().startswith(b'PK')
        processor.last_added_book_ids = [7]
    monkeypatch.setattr(processor, 'add_book_to_library', recovered)
    processor.ingest_acsm()
    assert len(hooks) == 1
    assert Path(processor.filepath).read_text() == 'owned-ticket'


def test_fulfillment_preserves_ticket_basename_for_filename_metadata(monkeypatch, tmp_path):
    import sys
    from contextlib import nullcontext
    import calibre_ticket_fulfillment as helper
    source = tmp_path / 'First Book.acsm'
    source.write_text('owned-ticket')
    def hook(paths):
        staged = Path(paths[0])
        assert staged.name == source.name
        output = staged.parent / 'plugin-generic.pdf'
        output.write_bytes(b'%PDF-1.4\n% owned metadata-free test document\n%%EOF')
        return [str(output)]
    monkeypatch.setitem(sys.modules, 'calibre.db.adding', types.SimpleNamespace(
        run_import_plugins=hook, run_import_plugins_before_metadata=lambda p: nullcontext()))
    result = helper.fulfill_ticket(source, tmp_path / 'result')
    assert Path(result['path']).name == 'First Book.pdf'


@pytest.mark.parametrize('damage', ['missing_manifest', 'modified_book', 'wrong_ticket', 'escape'])
def test_damaged_recovery_entry_preserves_source_without_refilling(monkeypatch, tmp_path, damage):
    from calibre_ticket_fulfillment import persist_result, file_digest
    processor = _processor(monkeypatch, tmp_path)
    digest = file_digest(processor.filepath)
    destination = tmp_path / 'processed_books' / 'acsm_fulfilled' / digest
    destination.mkdir(parents=True)
    book = _epub(destination / 'ticket.epub')
    persist_result(destination, digest, book)
    manifest = destination / 'result.json'
    if damage == 'missing_manifest':
        manifest.unlink()
    elif damage == 'modified_book':
        book.write_bytes(b'changed')
    else:
        data = json.loads(manifest.read_text())
        data['source_sha256' if damage == 'wrong_ticket' else 'file'] = ('0' * 64 if damage == 'wrong_ticket' else '../escape.epub')
        manifest.write_text(json.dumps(data))
    monkeypatch.setattr(ingest_processor, '_run_converter_streaming', lambda *a, **k: pytest.fail('damaged recovery must not spend ticket again'))
    with pytest.raises(ingest_processor.PreserveIngestSourceError): processor.ingest_acsm()
    assert Path(processor.filepath).read_text() == 'owned-ticket'
    assert destination.exists()
