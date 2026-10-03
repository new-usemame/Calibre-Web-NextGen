# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for fork issue #312 — Tier 1: logger dual-write.

When `cps.logger.setup()` is called with a regular file path, it must
attach BOTH a `StreamHandler` (for `docker logs`) AND a
`RotatingFileHandler` (so the admin → View Logs UI has content to
render). Before this change the logger replaced root with a single
handler, and `cps.config_sql` further force-reset every install's
`config_logfile` to `/dev/stdout`, which left the admin log viewer
permanently empty.

Pin the new contract:

* setup(<file path>) → stdout and rotating file when their sinks differ;
  a shared file gets one rotating writer, including after rollover (#1613)
* setup(LOG_TO_STDOUT) → 1 root handler: stdout StreamHandler, no file
* rotation defaults: 5 MiB × 5 backups (was 100 KB × 2 — useless)
* setup() returns the path written to, or "" for default, or LOG_TO_STDOUT for stdout-only
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from logging import StreamHandler
from logging.handlers import RotatingFileHandler

import pytest

import cps.logger as cwa_logger


@pytest.fixture
def reset_root():
    """Snapshot/restore the root logger handlers so tests don't leak."""
    root = logging.root
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)


def _file_handlers():
    return [h for h in logging.root.handlers if isinstance(h, RotatingFileHandler)]


def _stream_handlers_to_stdout():
    out = []
    for h in logging.root.handlers:
        if isinstance(h, StreamHandler) and not isinstance(h, RotatingFileHandler):
            stream = getattr(h, "stream", None)
            if stream is sys.stdout or getattr(h, "baseFilename", "") == cwa_logger.LOG_TO_STDOUT:
                out.append(h)
    return out


@pytest.mark.unit
class TestDualHandlerSetup:
    @pytest.mark.parametrize('failure', ['directory_permissions', 'descriptor_path', 'partial_rename'])
    def test_failed_rollover_still_persists_shared_records(self, tmp_path, reset_root, monkeypatch, failure):
        path = tmp_path / 'shared.log'
        with path.open('a', encoding='utf-8') as redirected:
            monkeypatch.setattr(sys, 'stdout', redirected)
            target = '/dev/fd/' + str(redirected.fileno()) if failure == 'descriptor_path' else str(path)
            cwa_logger.setup(target, logging.INFO)
            handler = _file_handlers()[0]
            handler.maxBytes = 1
            if failure == 'partial_rename':
                rotate = handler.rotate

                def lose_directory_write_permission(source, destination):
                    rotate(source, destination)
                    tmp_path.chmod(0o500)
                    raise PermissionError('directory became unwritable after rename')

                monkeypatch.setattr(handler, 'rotate', lose_directory_write_permission)
            try:
                if failure == 'directory_permissions':
                    tmp_path.chmod(0o500)
                for index in range(3):
                    logging.getLogger('cps.failed_rollover_test').info('failed-rollover-marker-%d', index)
                redirected.flush()
                for index in range(3):
                    assert sum(p.read_text().count('failed-rollover-marker-' + str(index))
                               for p in tmp_path.glob('shared.log*')) == 1
            finally:
                tmp_path.chmod(0o700)

    def test_rollover_during_settings_reload_keeps_one_active_writer(self, tmp_path, reset_root, monkeypatch):
        path = tmp_path / 'shared.log'
        with path.open('a', encoding='utf-8') as redirected:
            monkeypatch.setattr(sys, 'stdout', redirected)
            cwa_logger.setup(str(path), logging.INFO)
            previous = _file_handlers()[0]
            previous.doRollover()
            original = cwa_logger._make_file_handler
            started = threading.Event()
            completed = threading.Event()
            workers = []

            def rotate():
                started.set()
                with previous.lock:
                    previous.doRollover()
                completed.set()

            def interleave(*args, **kwargs):
                created = original(*args, **kwargs)
                worker = threading.Thread(target=rotate)
                workers.append(worker)
                worker.start()
                assert started.wait(2)
                # The broken replacement permits rollover here. A coherent
                # handoff blocks it until setup releases the existing writer.
                completed.wait(0.1)
                return created

            monkeypatch.setattr(cwa_logger, '_make_file_handler', interleave)
            try:
                cwa_logger.setup(str(path), logging.INFO)
            finally:
                for worker in workers:
                    worker.join(2)
                    assert not worker.is_alive()
            logging.getLogger('cps.concurrent_sink_test').info('concurrent-reload-marker')
            redirected.flush()
            assert path.read_text().count('concurrent-reload-marker') == 1
            assert sum(p.read_text().count('concurrent-reload-marker')
                       for p in tmp_path.glob('shared.log*')) == 1

    @pytest.mark.parametrize('alias', ['same', 'symlink', 'hardlink'])
    def test_shared_file_sink_writes_once_and_follows_rotation(self, tmp_path, reset_root, monkeypatch, alias):
        path = tmp_path / 'shared.log'
        path.touch()
        stdout_path = path
        if alias != 'same':
            stdout_path = tmp_path / 'stdout.log'
            if alias == 'symlink':
                stdout_path.symlink_to(path)
            else:
                os.link(path, stdout_path)
        with stdout_path.open('a', encoding='utf-8') as redirected:
            monkeypatch.setattr(sys, 'stdout', redirected)
            assert cwa_logger.setup(str(path), logging.INFO) == str(path)
            # Settings can be reapplied; the old file handler must close
            # without reintroducing a second writer to the same sink.
            assert cwa_logger.setup(str(path), logging.INFO) == str(path)
            log = logging.getLogger('cps.shared_sink_test')
            log.info('shared-before-rotation')
            redirected.flush()
            assert path.read_text().count('shared-before-rotation') == 1
            # Force the next emitted record to roll the actual file. The
            # inherited stdout descriptor still names the old inode.
            _file_handlers()[0].maxBytes = 1
            log.info('shared-after-rotation')
            redirected.flush()
            assert path.read_text().count('shared-after-rotation') == 1
            backup = Path(str(path) + '.1').read_text()
            assert backup.count('shared-before-rotation') == 1
            assert 'shared-after-rotation' not in backup
            # ConfigSQL reloads logging after settings saves. Stdout now
            # points to the backup, but it still belongs to this log sink.
            cwa_logger.setup(str(path), logging.INFO)
            log.info('shared-after-reload')
            redirected.flush()
            assert path.read_text().count('shared-after-reload') == 1
            assert 'shared-after-reload' not in Path(str(path) + '.1').read_text()

    @pytest.mark.parametrize('change', ['target', 'stdout_object', 'stdout_descriptor'])
    def test_changed_sink_after_rotation_keeps_both_outputs(self, tmp_path, reset_root, monkeypatch, change):
        path = tmp_path / 'shared.log'
        output = tmp_path / 'new-stdout.log'
        with path.open('a', encoding='utf-8') as redirected, output.open('a', encoding='utf-8') as replacement:
            monkeypatch.setattr(sys, 'stdout', redirected)
            cwa_logger.setup(str(path), logging.INFO)
            _file_handlers()[0].doRollover()
            if change == 'target':
                path = tmp_path / 'new-target.log'
                stdout_path = tmp_path / 'shared.log.1'
            elif change == 'stdout_object':
                monkeypatch.setattr(sys, 'stdout', replacement)
                stdout_path = output
            else:
                os.dup2(replacement.fileno(), redirected.fileno())
                stdout_path = output
            cwa_logger.setup(str(path), logging.INFO)
            logging.getLogger('cps.changed_sink_test').info('changed-sink-marker')
            redirected.flush()
            replacement.flush()
            assert path.read_text().count('changed-sink-marker') == 1
            assert stdout_path.read_text().count('changed-sink-marker') == 1

    def test_fallback_target_shared_with_stdout_writes_once(self, tmp_path, reset_root, monkeypatch):
        fallback = tmp_path / 'fallback.log'
        requested = tmp_path / 'missing-directory' / 'requested.log'
        make_file_handler = cwa_logger._make_file_handler
        monkeypatch.setattr(cwa_logger, '_make_file_handler',
                            lambda path: make_file_handler(path, default_path=str(fallback)))
        with fallback.open('a', encoding='utf-8') as redirected:
            monkeypatch.setattr(sys, 'stdout', redirected)
            assert cwa_logger.setup(str(requested), logging.INFO) == ''
            logging.getLogger('cps.shared_sink_test').info('fallback-single-record')
            redirected.flush()
            assert fallback.read_text().count('fallback-single-record') == 1
            _file_handlers()[0].doRollover()
            assert cwa_logger.setup(str(requested), logging.INFO) == ''
            logging.getLogger('cps.shared_sink_test').info('fallback-after-reload')
            redirected.flush()
            assert fallback.read_text().count('fallback-after-reload') == 1
            assert 'fallback-after-reload' not in Path(str(fallback) + '.1').read_text()

    def test_distinct_regular_stdout_and_logfile_both_receive_one_record(self, tmp_path, reset_root, monkeypatch):
        path = tmp_path / 'app.log'
        output = tmp_path / 'service-output.log'
        with output.open('a', encoding='utf-8') as redirected:
            monkeypatch.setattr(sys, 'stdout', redirected)
            cwa_logger.setup(str(path), logging.INFO)
            logging.getLogger('cps.distinct_sink_test').info('distinct-sink-marker')
            redirected.flush()
            assert path.read_text().count('distinct-sink-marker') == 1
            assert output.read_text().count('distinct-sink-marker') == 1

    def test_file_path_attaches_both_stdout_and_file_handler(self, tmp_path, reset_root):
        path = tmp_path / "calibre-web.log"
        cwa_logger.setup(str(path), logging.INFO)
        files = _file_handlers()
        stdouts = _stream_handlers_to_stdout()
        assert files, (
            "expected a RotatingFileHandler so admin → View Logs has content; "
            f"root handlers were: {logging.root.handlers!r}"
        )
        assert stdouts, (
            "expected a stdout StreamHandler so `docker logs` keeps working; "
            f"root handlers were: {logging.root.handlers!r}"
        )

    def test_stdout_only_path_has_no_file_handler(self, reset_root):
        cwa_logger.setup(cwa_logger.LOG_TO_STDOUT, logging.INFO)
        assert _file_handlers() == [], (
            "LOG_TO_STDOUT must remain a single-handler stdout configuration; "
            f"root handlers were: {logging.root.handlers!r}"
        )
        assert _stream_handlers_to_stdout(), "must keep the stdout handler"

    def test_emitted_record_lands_in_the_file(self, tmp_path, reset_root):
        path = tmp_path / "calibre-web.log"
        cwa_logger.setup(str(path), logging.INFO)
        logging.getLogger("cps.unit_test").info("hello-from-test")
        # RotatingFileHandler buffers via the underlying stream — flush
        # by closing/reopening for read.
        for h in _file_handlers():
            h.flush()
        body = path.read_text(encoding="utf-8")
        assert "hello-from-test" in body, body

    def test_emitted_record_also_lands_on_stdout(self, tmp_path, reset_root, capsys):
        path = tmp_path / "calibre-web.log"
        cwa_logger.setup(str(path), logging.INFO)
        logging.getLogger("cps.unit_test").info("dual-write-marker")
        captured = capsys.readouterr()
        assert "dual-write-marker" in captured.out, (
            "stdout handler must mirror records so `docker logs` keeps working; "
            f"stdout was: {captured.out!r}"
        )


@pytest.mark.unit
class TestRotationDefaults:
    def test_rotation_uses_5mib_maxbytes_and_5_backups(self, tmp_path, reset_root):
        """100 KB × 2 backups was too small for any real debug session."""
        path = tmp_path / "calibre-web.log"
        cwa_logger.setup(str(path), logging.INFO)
        files = _file_handlers()
        assert files, "expected a file handler"
        fh = files[0]
        assert fh.maxBytes >= 5 * 1024 * 1024, (
            f"rotation maxBytes must be ≥5 MiB, got {fh.maxBytes!r}"
        )
        assert fh.backupCount >= 5, (
            f"backupCount must be ≥5 (≥30 MiB of retained logs), got {fh.backupCount!r}"
        )


@pytest.mark.unit
class TestSetupReturnContract:
    def test_default_path_returns_empty_string(self, tmp_path, reset_root, monkeypatch):
        # Point the default at a temp dir so we don't pollute /config
        monkeypatch.setattr(cwa_logger, "DEFAULT_LOG_FILE",
                            str(tmp_path / "calibre-web.log"))
        out = cwa_logger.setup("", logging.INFO)
        assert out == "", f"empty path should resolve to default and report empty; got {out!r}"

    def test_stdout_token_returns_stdout_token(self, reset_root):
        out = cwa_logger.setup(cwa_logger.LOG_TO_STDOUT, logging.INFO)
        assert out == cwa_logger.LOG_TO_STDOUT, (
            f"stdout token round-trips so callers can detect it; got {out!r}"
        )

    def test_custom_path_returns_absolute_custom_path(self, tmp_path, reset_root):
        path = tmp_path / "custom.log"
        out = cwa_logger.setup(str(path), logging.INFO)
        assert out == os.path.abspath(str(path)), (
            f"custom path should round-trip as absolute; got {out!r}"
        )
