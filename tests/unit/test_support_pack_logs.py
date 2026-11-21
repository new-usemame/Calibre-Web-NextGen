# SPDX-License-Identifier: GPL-3.0-or-later
"""Log handling in the sanitised support pack (fork issue #2728).

Routine APScheduler per-run lines dominate old logs (a job every 5 seconds is
17,280 pairs a day) and say nothing a maintainer needs, so the sanitised pack
counts them instead of carrying them. These tests pin what is dropped, what
must NOT be, and that the size cap spends its budget on lines worth reading.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from cps import debug_info

pytestmark = pytest.mark.unit

RUNNING = ('[2026-11-20 19:31:44,001]  INFO {apscheduler.executors.default:123} '
           'Running job "acquisition import queue (trigger: interval[0:00:05], next run at: '
           '2026-11-20 19:31:49 AEST)" (scheduled at 2026-11-20 19:31:44.000000+10:00)\n')
SUCCESS = ('[2026-11-20 19:31:44,123]  INFO {apscheduler.executors.default:156} '
           'Job "acquisition import queue (trigger: interval[0:00:05], next run at: '
           '2026-11-20 19:31:49 AEST)" executed successfully\n')
ERRORED = ('[2026-11-20 19:31:50,000] ERROR {apscheduler.executors.default:162} '
           'Job "acquisition import queue (trigger: interval[0:00:05])" raised an exception\n')
MISSED = ('[2026-11-20 19:31:51,000]  WARNING {apscheduler.executors.default:140} '
          'Run time of job "acquisition import queue" was missed by 0:00:03\n')
OTHER_LOGGER = ('[2026-11-20 19:31:52,000]  INFO {apscheduler.scheduler:200} '
                'Added job "acquisition import queue" to job store "default"\n')
REAL = '[2026-11-20 19:32:00,000]  INFO {cps.web:100} Login ok\n'


class TestRoutineLineMatching:
    @pytest.mark.parametrize("line", [RUNNING, SUCCESS])
    def test_routine_run_lines_match(self, line):
        assert debug_info._is_routine_scheduler_line(line)

    @pytest.mark.parametrize("line", [ERRORED, MISSED, OTHER_LOGGER, REAL])
    def test_failures_warnings_and_other_loggers_do_not_match(self, line):
        assert not debug_info._is_routine_scheduler_line(line)

    def test_an_error_that_quotes_the_routine_words_is_kept(self):
        # Matching is on the INFO level of the executor logger, not on the words.
        line = ('[2026-11-20 19:31:50,000] ERROR {apscheduler.executors.default:162} '
                'Job "x" executed successfully but then failed to record it\n')
        assert not debug_info._is_routine_scheduler_line(line)


class TestSanitizeLogBytes:
    def _run(self, *lines, scrubber=None):
        out = debug_info._sanitize_log_bytes("".join(lines).encode("utf-8"), scrubber)
        return out.decode("utf-8")

    def test_drops_only_routine_lines_and_counts_them(self):
        text = self._run(REAL, RUNNING, SUCCESS, ERRORED, MISSED, SUCCESS, RUNNING)
        assert RUNNING.strip() not in text and SUCCESS.strip() not in text
        assert ERRORED.strip() in text and MISSED.strip() in text and REAL.strip() in text
        assert text.startswith("[support pack] Omitted 4 routine APScheduler per-run lines")
        assert "between 2026-11-20 19:31:44 and 2026-11-20 19:31:44" in text

    def test_range_spans_first_to_last_regardless_of_order(self):
        late = SUCCESS.replace("19:31:44,123", "23:59:59,000")
        early = SUCCESS.replace("19:31:44,123", "01:02:03,000")
        text = self._run(late, early)
        assert "between 2026-11-20 01:02:03 and 2026-11-20 23:59:59" in text

    def test_no_header_when_nothing_was_omitted(self):
        text = self._run(REAL, ERRORED)
        assert "[support pack]" not in text
        assert text == REAL + ERRORED

    def test_kept_lines_still_go_through_the_scrubber(self):
        seen = []

        def scrubber(line):
            seen.append(line)
            return line.replace("Login ok", "Login <scrubbed>")
        text = self._run(SUCCESS, REAL, SUCCESS, scrubber=scrubber)
        assert "Login <scrubbed>" in text
        # Routine lines never reach the scrubber: that is where the time goes.
        assert seen == [REAL]

    def test_many_routine_lines_collapse_to_one_note(self):
        text = self._run(*([RUNNING, SUCCESS] * 5000), REAL)
        assert text.count("\n") == 2
        assert "Omitted 10,000 routine" in text

    def test_cap_is_spent_on_useful_lines_not_noise(self, monkeypatch):
        useful = [f"[2026-11-20 19:32:{i:02d},000]  INFO {{cps.web:1}} request {i:02d}\n" for i in range(20)]
        line_bytes = len(useful[0].encode())
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", line_bytes * 10)
        # Isolate the ordering (drop noise, then cap) from the separate guard on
        # pathologically large files, which would trip on this tiny cap.
        monkeypatch.setattr(debug_info, "_RAW_READ_FACTOR", 10_000)
        # 500 noise lines interleaved: a raw-byte tail cut would keep almost no real lines.
        mixed = []
        for index, line in enumerate(useful):
            mixed.extend([SUCCESS] * 25)
            mixed.append(line)
        text = self._run(*mixed)
        kept = [ln for ln in text.splitlines() if "request " in ln]
        assert kept == [ln.rstrip("\n") for ln in useful[-10:]]  # the 10 most recent

    def test_pathological_file_is_bounded_and_starts_on_a_whole_line(self, monkeypatch):
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", 100)
        lines = [f"[2026-11-20 19:00:{i % 60:02d},000]  INFO {{x:1}} line number {i:04d}\n" for i in range(200)]
        out = self._run(*lines)
        assert len(out.encode()) <= 100
        assert out.endswith(lines[-1])
        assert all(ln.startswith("[2026-") for ln in out.splitlines())

    def test_empty_input_is_returned_unchanged(self):
        assert debug_info._sanitize_log_bytes(b"") == b""


class TestInBuiltPack:
    @pytest.fixture
    def built(self, tmp_path, monkeypatch):
        log = tmp_path / "calibre-web.log"
        log.write_text(SUCCESS * 50 + REAL + ERRORED, encoding="utf-8")

        class _Cfg:
            config_logfile = str(log)
            config_access_logfile = str(tmp_path / "access.log")
            config_access_log = 0

            def to_dict(self):
                return {"config_port": 8083}

        monkeypatch.setattr(debug_info, "config", _Cfg())
        monkeypatch.setattr(debug_info, "collect_stats", lambda: {"python": "3"})
        monkeypatch.setattr(debug_info, "_known_usernames", lambda: [])
        monkeypatch.setattr(debug_info, "_resolve_state_paths", lambda: {})
        monkeypatch.setattr(debug_info, "_resolve_integration_inputs", lambda: {})
        return debug_info

    def _log(self, built, sanitize):
        with zipfile.ZipFile(built._build_debug_zip(sanitize=sanitize)) as zf:
            return zf.read("calibre-web.log").decode(), zf.read("SANITIZED.txt").decode() if sanitize else None

    def test_sanitised_pack_omits_and_says_so(self, built):
        log, note = self._log(built, True)
        assert SUCCESS.strip() not in log
        assert log.startswith("[support pack] Omitted 50 routine APScheduler per-run lines")
        assert ERRORED.strip() in log
        assert "routine APScheduler per-run lines" in note and "omitted" in note

    def test_unredacted_debug_pack_keeps_every_line(self, built):
        log, _ = self._log(built, False)
        assert log.count("executed successfully") == 50
        assert "[support pack]" not in log
