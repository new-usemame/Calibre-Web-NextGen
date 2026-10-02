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
        book = _epub(dest / 'fulfilled.epub')
        fulfilled.append(str(book))
        return 'CWNG_FULFILLMENT_RESULT=' + json.dumps({'path': str(book), 'format': 'epub'})
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
    assert list((tmp_path / 'result').iterdir()) == []


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
