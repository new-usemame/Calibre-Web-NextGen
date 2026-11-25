# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading logs into the support pack safely (review of #3013, fork issue #2728).

* The size cap is applied at the file handle, so a huge log is never read whole.
* The cap holds on what is written: scrubbing can lengthen lines and the omission
  note adds bytes, and neither may push a file over the limit.
* A log that rotates away between the directory listing and the read must not abort
  the pack; the manifest records what happened to each log.
* The traceback digest is fed from the very bytes that go into the archive.
"""

from __future__ import annotations

import io
import json
import os
import zipfile

import pytest

from cps import debug_info

pytestmark = pytest.mark.unit


class _CountingFile:
    """A real file that records how many bytes were actually read from it."""

    def __init__(self, path, mode, tally):
        self._file = open(path, mode)  # the builtin: this module patches the name in debug_info only
        self._tally = tally

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._file.close()

    def fileno(self):
        return self._file.fileno()

    def seek(self, *args):
        return self._file.seek(*args)

    def read(self, *args):
        data = self._file.read(*args)
        self._tally.append(len(data))
        return data


def _numbered_lines(count, width=60):
    return "".join(f"[2026-11-20 19:00:00,000]  INFO {{x:1}} line {i:06d}".ljust(width - 1) + "\n" for i in range(count))


class TestReadLogTail:
    def test_a_small_file_is_read_whole(self, tmp_path):
        path = tmp_path / "a.log"
        path.write_bytes(b"one\ntwo\n")
        assert debug_info._read_log_tail(str(path), 1000) == (b"one\ntwo\n", False)

    def test_exactly_the_limit_is_not_cut(self, tmp_path):
        path = tmp_path / "a.log"
        path.write_bytes(b"x" * 100)
        data, was_cut = debug_info._read_log_tail(str(path), 100)
        assert len(data) == 100 and was_cut is False

    def test_a_big_file_gives_the_tail_starting_on_a_whole_line(self, tmp_path):
        path = tmp_path / "a.log"
        path.write_text(_numbered_lines(2000))
        data, was_cut = debug_info._read_log_tail(str(path), 1000)
        lines = data.decode().splitlines()
        assert was_cut is True and len(data) <= 1000
        assert lines[-1].startswith("[2026-11-20 19:00:00,000]  INFO {x:1} line 001999")
        assert all(line.startswith("[2026-") for line in lines)  # the cut first line is gone

    def test_a_tail_with_no_newline_is_returned_as_it_is(self, tmp_path):
        path = tmp_path / "a.log"
        path.write_bytes(b"y" * 500)
        data, was_cut = debug_info._read_log_tail(str(path), 100)
        assert data == b"y" * 100 and was_cut is True

    def test_the_read_is_bounded_at_the_handle_not_after(self, tmp_path, monkeypatch):
        path = tmp_path / "a.log"
        path.write_text(_numbered_lines(50_000))  # about 3 MB
        tally = []
        monkeypatch.setattr(debug_info, "open", lambda p, m: _CountingFile(p, m, tally), raising=False)
        debug_info._read_log_tail(str(path), 4000)
        assert sum(tally) <= 4000  # never the whole 3 MB

    def test_a_missing_file_raises_for_the_caller_to_record(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            debug_info._read_log_tail(str(tmp_path / "gone.log"), 100)


class TestFinalCapHoldsOnWhatIsWritten:
    def test_scrubbing_that_lengthens_lines_cannot_push_the_file_over_the_cap(self, monkeypatch):
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", 600)
        monkeypatch.setattr(debug_info, "_RAW_READ_FACTOR", 1000)
        raw = "".join(f"[2026-11-20 19:00:00,000] INFO {{x:1}} mail a{i}@b.co\n" for i in range(40))
        assert len(raw.encode()) > 600  # the raw text is cut first
        grow = lambda line: line.replace("@b.co", "@a-much-longer-address.example.org")  # noqa: E731
        out = debug_info._sanitize_log_bytes(raw.encode(), grow)
        assert len(out) <= 600
        assert out.decode().rstrip().endswith("a39@a-much-longer-address.example.org")  # still the newest lines

    def test_the_omission_note_counts_toward_the_cap(self, monkeypatch):
        noise = ('[2026-11-20 19:31:44,123]  INFO {apscheduler.executors.default:156} Job "j" executed successfully\n')
        useful = "".join(f"[2026-11-20 19:32:{i:02d},000]  INFO {{x:1}} real line {i:02d}\n" for i in range(30))
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", 700)
        monkeypatch.setattr(debug_info, "_RAW_READ_FACTOR", 1000)
        out = debug_info._sanitize_log_bytes((noise * 5 + useful).encode(), lambda line: line)
        assert len(out) <= 700 and out.decode().startswith("[support pack] Omitted 5 routine")

    def test_a_cap_too_small_for_the_note_drops_the_note_and_keeps_the_cap(self, monkeypatch):
        noise = ('[2026-11-20 19:31:44,123]  INFO {apscheduler.executors.default:156} Job "j" executed successfully\n')
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", 80)
        monkeypatch.setattr(debug_info, "_RAW_READ_FACTOR", 1000)
        out = debug_info._sanitize_log_bytes((noise + "[2026-11-20 19:32:00,000] INFO {x:1} hi\n").encode(), lambda line: line)
        assert len(out) <= 80 and b"Omitted" not in out and out.endswith(b"hi\n")


class TestInBuiltPack:
    TRACEBACK = ('[2026-11-17 08:15:02,100]  ERROR {cps.x:1} first read\nTraceback (most recent call last):\n'
                 '  File "/a/one.py", line 1, in first\n    go()\nValueError: from the first read\n')

    @pytest.fixture
    def pack(self, tmp_path, monkeypatch):
        for name, text in (("calibre-web.log", self.TRACEBACK), ("calibre-web.log.1", "[2026-11-16 09:00:00,000] INFO {x:1} older\n"),
                           ("calibre-web.log.2", "[2026-11-15 09:00:00,000] INFO {x:1} oldest\n"),
                           ("access.log", "1.2.3.4 - - [x] GET / 200\n")):
            (tmp_path / name).write_text(text)

        class _Cfg:
            config_logfile = str(tmp_path / "calibre-web.log")
            config_access_logfile = str(tmp_path / "access.log")
            config_access_log = 1

            def to_dict(self):
                return {"config_port": 8083}

        monkeypatch.setattr(debug_info, "config", _Cfg())
        monkeypatch.setattr(debug_info, "collect_stats", lambda: {"python": "3"})
        monkeypatch.setattr(debug_info, "_known_usernames", lambda: [])
        monkeypatch.setattr(debug_info, "_resolve_state_paths", lambda: {})
        monkeypatch.setattr(debug_info, "_resolve_integration_inputs", lambda: {})
        return tmp_path

    def _build(self, sanitize):
        with zipfile.ZipFile(debug_info._build_debug_zip(sanitize=sanitize)) as zf:
            return zf.namelist(), {n: zf.read(n).decode() for n in zf.namelist()}

    def _logs(self, files):
        return {entry["file"]: entry for entry in json.loads(files["manifest.json"])["logs"]}

    def test_the_manifest_says_what_happened_to_every_log(self, pack):
        _names, files = self._build(True)
        logs = self._logs(files)
        assert set(logs) == {"calibre-web.log", "calibre-web.log.1", "calibre-web.log.2", "access.log"}
        assert all(entry["status"] == "included" and entry["bytes"] > 0 and entry["cut_to_tail"] is False
                   for entry in logs.values())

    @pytest.mark.parametrize("sanitize", [True, False])
    def test_a_log_that_rotated_away_does_not_abort_the_pack(self, pack, monkeypatch, sanitize):
        real = debug_info._read_log_tail

        def rotating(path, limit):
            if os.path.basename(path) == "calibre-web.log.1":
                raise FileNotFoundError(path)  # gone between the glob and the read
            return real(path, limit)

        monkeypatch.setattr(debug_info, "_read_log_tail", rotating)
        names, files = self._build(sanitize)
        assert "calibre-web.log.1" not in names
        assert {"calibre-web.log", "calibre-web.log.2", "access.log", "tracebacks.txt", "manifest.json"} <= set(names)
        assert self._logs(files)["calibre-web.log.1"] == {"file": "calibre-web.log.1", "status": "missing"}
        assert "from the first read" in files["tracebacks.txt"]  # the rest of the pack is intact

    def test_an_unreadable_log_is_recorded_as_unreadable(self, pack, monkeypatch):
        real = debug_info._read_log_tail

        def locked_down(path, limit):
            if os.path.basename(path) == "access.log":
                raise PermissionError(path)
            return real(path, limit)

        monkeypatch.setattr(debug_info, "_read_log_tail", locked_down)
        names, files = self._build(True)
        assert "access.log" not in names and self._logs(files)["access.log"]["status"] == "unreadable"

    def test_the_digest_comes_from_the_same_bytes_as_the_archive_even_if_the_file_changes(self, pack, monkeypatch):
        real = debug_info._read_log_tail
        calls = []

        def changing(path, limit):
            result = real(path, limit)
            calls.append(os.path.basename(path))
            # The file is rewritten the moment after it is read, as a rotation or a busy logger would.
            if os.path.basename(path) == "calibre-web.log":
                (pack / "calibre-web.log").write_text("[2026-11-20 10:00:00,000] INFO {x:1} rewritten, no traceback\n")
            return result

        monkeypatch.setattr(debug_info, "_read_log_tail", changing)
        _names, files = self._build(False)  # the unredacted pack is the one that used to re-read
        assert calls.count("calibre-web.log") == 1  # read once, not read again for the digest
        assert "from the first read" in files["calibre-web.log"]
        assert "from the first read" in files["tracebacks.txt"]  # describes what the archive holds
        assert "rewritten" not in files["calibre-web.log"] and "rewritten" not in files["tracebacks.txt"]

    def test_a_log_over_the_cap_but_within_the_read_bound_is_reported_as_cut(self, pack, monkeypatch):
        # Read whole (it is under the read bound), then trimmed to the cap when written:
        # the manifest has to say so, or a log that lost its head looks complete.
        (pack / "calibre-web.log").write_text(_numbered_lines(60_000))  # about 3.6 MB
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", 1_000_000)  # read bound is 4x: 4 MB
        _names, files = self._build(True)
        entry = self._logs(files)["calibre-web.log"]
        assert entry["cut_to_tail"] is True and entry["bytes"] <= 1_000_000
        assert "line 059999" in files["calibre-web.log"] and "line 000000" not in files["calibre-web.log"]

    def test_a_log_that_fits_is_not_reported_as_cut_even_when_routine_lines_are_counted(self, pack):
        noise = '[2026-11-20 19:31:44,123]  INFO {apscheduler.executors.default:156} Job "j" executed successfully\n' * 50
        (pack / "calibre-web.log").write_text(noise + "[2026-11-20 19:33:00,000]  INFO {x:1} kept\n")
        _names, files = self._build(True)
        entry = self._logs(files)["calibre-web.log"]
        assert entry["cut_to_tail"] is False          # nothing was lost to the size cap...
        assert "kept" in files["calibre-web.log"]     # ...the routine lines are counted, and say so
        assert "Omitted 50 routine" in files["calibre-web.log"].splitlines()[0]

    def test_the_cut_is_reported_when_scrubbing_pushes_the_result_over_the_cap(self, pack, monkeypatch):
        # Under the cap as read, over it once an address becomes a longer placeholder.
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", 5_000)
        lines = "".join(f"[2026-11-20 19:00:00,000]  INFO {{x:1}} user a{i:03d}@b.co" + "\n" for i in range(80))
        assert len(lines.encode()) < 5_000
        (pack / "calibre-web.log").write_text(lines)
        monkeypatch.setattr(debug_info, "_LogScrubber", lambda **kw: (lambda line: line.replace("@b.co", "@" + "x" * 40)))
        _names, files = self._build(True)
        entry = self._logs(files)["calibre-web.log"]
        assert entry["cut_to_tail"] is True and entry["bytes"] <= 5_000

    def test_a_huge_log_is_bounded_when_read_and_when_written(self, pack, monkeypatch):
        (pack / "calibre-web.log").write_text(_numbered_lines(60_000))  # about 3.6 MB
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", 10_000)
        monkeypatch.setattr(debug_info, "_RAW_READ_FACTOR", 2)
        tally = []
        monkeypatch.setattr(debug_info, "open", lambda p, m: _CountingFile(p, m, tally), raising=False)
        _names, files = self._build(True)
        assert max(tally) <= 20_000  # no single read pulled in more than the bound
        entry = self._logs(files)["calibre-web.log"]
        assert entry["cut_to_tail"] is True and entry["bytes"] <= 10_000
        assert len(files["calibre-web.log"].encode()) <= 10_000
