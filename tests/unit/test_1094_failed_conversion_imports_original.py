# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fork #1094: a book whose conversion failed vanished from the library.

Reported by @auspex against v4.1.20: a 63MB PDF was picked up, spent 45
minutes in ``ebook-convert``, got killed by the service safety timeout, and
then simply was not there. The library had no entry for it and the ingest
folder no longer held the file.

The cause was structural, not timeout-specific. ``main()`` imported the book
only inside ``if convert_successful:`` and had no ``else``, so *every*
conversion failure — timeout, unsupported internals, a corrupt source, a
missing plugin — returned without ever calling ``add_book_to_library()``,
while the ``finally`` block deleted the source from the ingest folder. The
two sibling branches that decline to convert (format in the ignore list,
auto-convert switched off) both import the original already, so the fallback
makes the behaviour consistent rather than introducing a new rule.

Pinned behaviour:
  1. A failed ``convert_book()`` still imports the ORIGINAL file.
  2. A failed ``convert_to_kepub()`` does the same.
  3. A SUCCESSFUL conversion still imports the CONVERTED file and not the
     original (anti-vacuity — a fallback that always fires would pass 1 and 2
     while breaking the normal path).
  4. ``backup()`` names the absolute destination directory it wrote to, so
     "moved to failed backup" is actionable instead of a dead end.
  5. The ingest service no longer claims the processor "should have timed out
     internally", because it previously had no conversion timeout at all —
     ``ingest_timeout_minutes`` bounded only the file-stability wait.
  6. The processor now owns a conversion deadline just inside the service's
     hard ``timeout``, derived from it in one place. Without that, the
     reporter's own case never reached the fallback at all: the supervisor
     SIGTERMed the processor mid-conversion, so ``main()`` never regained
     control. An overrun is now an ordinary failure, and the book is imported.
  7. Every non-success path of ``convert_to_kepub()`` returns a ``(bool, str)``
     pair. One returned ``None``, which made ``main()``'s tuple-unpack raise
     ``TypeError`` and skip the fallback — dropping the book for exactly the
     reason this issue is about.
  8. A failed kepub conversion backs up the original as well as any
     intermediate epub, so the failed folder holds the file the user dropped.
  9. The conversion deadline is ONE shared budget across both stages, not a
     fresh timeout per subprocess. A kepub ingest of a non-EPUB converts twice
     inside one hard timeout; two full deadlines could add up to more than the
     watchdog allowed, leaving the second conversion running when SIGTERM
     arrived — re-opening this very bug at the DEFAULT timeout setting.
 10. The deadline is monotonic in the hard timeout. A flat 30s reserve applied
     to a small timeout inverted the two (a 31s timeout left 1s to convert
     while a 30s timeout left 23s); the reserve is now capped so conversion
     always keeps the majority of the budget.

Findings 9 and 10, plus the OverflowError on a non-finite env value, came from
the cross-family review of PR #1157. The converter-child cleanup finding from
the same review is tracked separately in #1161.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest_processor  # noqa: E402

INGEST_SERVICE_RUN = (
    REPO_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run"
)
INGEST_PROCESSOR_PATH = REPO_ROOT / "scripts/ingest_processor.py"


def _shell_function_body(text, name):
    """Body of a shell function, from its opening line to the closing brace.

    A fixed byte window silently truncated mid-expression and failed on an
    edit that only made the function longer.
    """
    lines = text.splitlines()
    start = next(
        (i for i, l in enumerate(lines) if l.startswith(f"{name}() {{")), None
    )
    assert start is not None, f"{name}() not found in {INGEST_SERVICE_RUN}"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i] == "}"), None
    )
    assert end is not None, f"no closing brace for {name}()"
    return "\n".join(lines[start : end + 1])


def _conversion_processor(tmp_path):
    """Minimal real NewBookProcessor for exercising convert_book() directly."""
    nbp = object.__new__(ingest_processor.NewBookProcessor)
    nbp.filepath = str(tmp_path / "Big Handbook.pdf")
    Path(nbp.filepath).write_bytes(b"pdf bytes")
    nbp.filename = "Big Handbook.pdf"
    nbp.input_format = "pdf"
    nbp.target_format = "epub"
    nbp.tmp_conversion_dir = str(tmp_path) + os.sep
    nbp.calibre_env = os.environ.copy()
    nbp.cwa_settings = {"auto_backup_conversions": False}
    nbp.backed_up = []
    nbp.backup = lambda f, backup_type: nbp.backed_up.append((f, backup_type))
    return nbp


class _FakeProcessor:
    """Stands in for NewBookProcessor so main()'s real control flow runs."""

    def __init__(self, filepath, *, convert_result, target_format="epub"):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.input_format = "pdf"
        self.target_format = target_format
        self.tmp_conversion_dir = os.path.join(
            os.path.dirname(filepath), "tmp_conversion"
        ) + os.sep
        self.ingest_ignored_formats = []
        self.convert_ignored_formats = []
        self.convert_retained_formats = []
        self.cwa_settings = {"ingest_timeout_minutes": 15}
        self.is_target_format = False
        self.auto_convert_on = True
        self.can_convert = True
        self.last_added_book_id = None
        self._convert_result = convert_result
        # Recorded calls
        self.imported = []
        self.convert_book_calls = 0
        self.convert_to_kepub_calls = 0

    # -- flow stubs ----------------------------------------------------
    def is_file_in_use(self, timeout=None):
        return True

    def is_supported_audiobook(self):
        return False

    def convert_book(self, end_format=None):
        self.convert_book_calls += 1
        return self._convert_result

    def convert_to_kepub(self):
        self.convert_to_kepub_calls += 1
        return self._convert_result

    def add_book_to_library(self, filepath, *args, **kwargs):
        self.imported.append(filepath)

    def add_format_to_book(self, book_id, filepath):
        pass

    def set_library_permissions(self):
        pass

    def delete_current_file(self):
        pass


def _run_main(
    monkeypatch,
    tmp_path,
    *,
    convert_result,
    target_format="epub",
    convert_ignored_formats=(),
):
    """Drive the real main() over a fake processor, return (fake, source_path)."""
    source = tmp_path / "Big Handbook.pdf"
    source.write_bytes(b"%PDF-1.4 not really a pdf")

    holder = {}

    def _factory(filepath):
        fake = _FakeProcessor(
            filepath, convert_result=convert_result, target_format=target_format
        )
        fake.convert_ignored_formats = list(convert_ignored_formats)
        holder["fake"] = fake
        return fake

    monkeypatch.setattr(ingest_processor, "NewBookProcessor", _factory)
    monkeypatch.setattr(
        ingest_processor, "_acquire_process_lock_or_exit", lambda: None
    )

    rc = ingest_processor.main(str(source))
    assert rc == 0
    return holder["fake"], str(source)


class TestFailedConversionStillImports:
    def test_failed_conversion_imports_the_original(self, monkeypatch, tmp_path):
        """#1094: the reported symptom — conversion fails, book is gone."""
        fake, source = _run_main(
            monkeypatch, tmp_path, convert_result=(False, "")
        )

        assert fake.convert_book_calls == 1
        assert fake.imported == [source], (
            "a failed conversion must still import the original file; "
            f"add_book_to_library calls were {fake.imported!r}"
        )

    def test_failed_kepub_conversion_imports_the_original(
        self, monkeypatch, tmp_path
    ):
        fake, source = _run_main(
            monkeypatch,
            tmp_path,
            convert_result=(False, ""),
            target_format="kepub",
        )

        assert fake.convert_to_kepub_calls == 1
        assert fake.imported == [source]

    def test_successful_conversion_still_imports_the_converted_file(
        self, monkeypatch, tmp_path
    ):
        """Anti-vacuity: the fallback must not fire on the happy path."""
        converted = str(tmp_path / "tmp_conversion" / "Big Handbook.epub")
        fake, source = _run_main(
            monkeypatch, tmp_path, convert_result=(True, converted)
        )

        assert fake.imported == [converted]
        assert source not in fake.imported

    def test_ignored_format_is_imported_exactly_once(self, monkeypatch, tmp_path):
        """The ignore-list branch imports the original and then reports
        convert_successful=False. The fallback must not add a second copy —
        that would file the same book twice on every deliberately-unconverted
        format, which is worse than the bug being fixed."""
        fake, source = _run_main(
            monkeypatch,
            tmp_path,
            convert_result=(False, ""),
            convert_ignored_formats=("pdf",),
        )

        assert fake.convert_book_calls == 0, "no conversion should be attempted"
        assert fake.imported == [source], (
            "an ignored format must be imported exactly once; got "
            f"{len(fake.imported)} imports: {fake.imported!r}"
        )


class TestConversionDeadline:
    """The reporter's actual failure was the supervisor's hard `timeout`
    SIGTERMing the processor mid-conversion, so main() never regained control
    and the fallback could not run. Owning a deadline just inside the hard one
    turns that into an ordinary failure the fallback can handle."""

    def test_absent_env_means_no_deadline(self, monkeypatch):
        monkeypatch.delenv("CWA_CONVERSION_DEADLINE_SECONDS", raising=False)
        assert ingest_processor.conversion_deadline_seconds() is None

    @pytest.mark.parametrize(
        "raw,expected",
        [("900", 900), ("2430", 2430), ("30.0", 30), ("0", None), ("-5", None),
         ("", None), ("banana", None)],
    )
    def test_env_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("CWA_CONVERSION_DEADLINE_SECONDS", raw)
        assert ingest_processor.conversion_deadline_seconds() == expected

    def test_convert_book_passes_the_deadline_to_the_converter(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("CWA_CONVERSION_DEADLINE_SECONDS", "1234")
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured.update(kwargs)
            raise subprocess.TimeoutExpired(cmd, 1234)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        nbp = _conversion_processor(tmp_path)
        ok, out = nbp.convert_book()

        # The converter receives what is LEFT of the budget, not the raw total,
        # so two stages can't each spend the whole thing. In a unit test almost
        # nothing has elapsed, so it lands just under the total.
        assert captured.get("timeout") == pytest.approx(1234, abs=60)
        assert captured.get("timeout") <= 1234
        assert (ok, out) == (False, ""), (
            "a deadline overrun must be reported as an ordinary conversion "
            "failure so main() imports the original"
        )
        assert nbp.backed_up == [(str(nbp.filepath), "failed")]

    def test_converter_missing_is_a_failure_not_a_crash(
        self, monkeypatch, tmp_path
    ):
        def _fake_run(cmd, **kwargs):
            raise OSError(2, "No such file or directory")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        nbp = _conversion_processor(tmp_path)
        assert nbp.convert_book() == (False, "")

    def test_shell_derives_the_deadline_from_the_hard_timeout(self):
        """One source of truth for the two limits."""
        text = INGEST_SERVICE_RUN.read_text()
        assert "CWA_CONVERSION_DEADLINE_SECONDS" in text
        body = _shell_function_body(text, "run_processor_with_timeout")
        assert "safety_timeout - margin" in body, (
            "the in-process deadline must be derived from the hard timeout, "
            "not hard-coded alongside it"
        )
        assert "export CWA_CONVERSION_DEADLINE_SECONDS" in body
        assert "unset CWA_CONVERSION_DEADLINE_SECONDS" in body, (
            "`timeout 0` means no hard limit, so there is no envelope to sit "
            "inside and no deadline should be imposed"
        )


class TestConversionBudgetIsSharedAcrossStages:
    """A kepub ingest of a non-EPUB converts twice, back to back, inside ONE
    hard timeout. Giving each stage a fresh full deadline let the two add up to
    more than the watchdog allowed, so the second was still running when
    SIGTERM arrived — the exact book-losing failure #1094 is about, reachable
    at the DEFAULT timeout. The budget is therefore one shared allowance
    anchored to process start, not a per-subprocess timeout."""

    def test_second_stage_gets_less_than_the_first(self, monkeypatch):
        monkeypatch.setenv("CWA_CONVERSION_DEADLINE_SECONDS", "600")
        first = ingest_processor.conversion_budget_remaining()
        # Simulate the first conversion having burned real time. Anchored to
        # now, not to the import-time value: the suite's own runtime counts
        # against a budget that is deliberately measuring wall clock.
        monkeypatch.setattr(ingest_processor, "_PROCESS_START_MONOTONIC",
                            time.monotonic() - 400)
        second = ingest_processor.conversion_budget_remaining()

        assert second < first, "the second stage must not get a fresh full budget"
        assert second == pytest.approx(200, abs=5), (
            "after 400s of a 600s budget, ~200s must remain"
        )

    def test_two_stages_cannot_outlast_the_hard_timeout(self, monkeypatch):
        """The property that actually protects the book: whatever the stages
        spend, the total stays inside the budget the watchdog sits outside."""
        monkeypatch.setenv("CWA_CONVERSION_DEADLINE_SECONDS", "600")
        for elapsed in (0, 100, 300, 599):
            monkeypatch.setattr(
                ingest_processor, "_PROCESS_START_MONOTONIC",
                time.monotonic() - elapsed
            )
            remaining = ingest_processor.conversion_budget_remaining()
            assert elapsed + remaining <= 600 + 1, (
                f"at {elapsed}s elapsed, a {remaining}s grant would overrun the budget"
            )

    def test_exhausted_budget_is_positive_not_negative(self, monkeypatch):
        """subprocess.run() raises ValueError on a negative timeout, which
        would escape the conversion-failure handlers and drop the book. An
        exhausted budget must instead expire immediately as a TimeoutExpired."""
        monkeypatch.setenv("CWA_CONVERSION_DEADLINE_SECONDS", "600")
        monkeypatch.setattr(ingest_processor, "_PROCESS_START_MONOTONIC",
                            time.monotonic() - 9000)
        remaining = ingest_processor.conversion_budget_remaining()
        assert remaining > 0
        assert remaining < 1

    def test_no_budget_when_unbounded(self, monkeypatch):
        monkeypatch.delenv("CWA_CONVERSION_DEADLINE_SECONDS", raising=False)
        assert ingest_processor.conversion_budget_remaining() is None

    @pytest.mark.parametrize("raw", ["inf", "Infinity", "1e309", "-inf"])
    def test_non_finite_env_is_ignored_not_raised(self, monkeypatch, raw):
        """float('inf') parses cleanly and only fails at int(), raising
        OverflowError — which is neither TypeError nor ValueError. Uncaught, it
        escaped from the `timeout=` argument before subprocess.run() was even
        called, bypassing the conversion-failure handlers entirely."""
        monkeypatch.setenv("CWA_CONVERSION_DEADLINE_SECONDS", raw)
        assert ingest_processor.conversion_deadline_seconds() is None
        assert ingest_processor.conversion_budget_remaining() is None

    def test_kepubify_also_receives_the_shared_budget(self):
        """Both converters must draw on the same allowance; a source-pin here
        because the two callsites are what the sharing property depends on."""
        source = INGEST_PROCESSOR_PATH.read_text()
        assert source.count("timeout=conversion_budget_remaining()") == 2, (
            "both ebook-convert and kepubify must use the shared budget, not "
            "a fresh per-subprocess deadline"
        )
        assert "timeout=conversion_deadline_seconds()" not in source, (
            "the raw total must not be handed to a subprocess — that is the "
            "per-stage deadline this class exists to prevent"
        )


class TestShellDeadlineArithmetic:
    """Derivation runs in the real service script, so it is executed here
    rather than eyeballed. A flat 30s reserve applied to a small timeout used
    to invert the two limits — a 31s timeout left 1s to convert while a 30s
    timeout left 23s — a cliff rather than a margin."""

    @staticmethod
    def _derive(safety_timeout):
        """Run the real derivation block out of the shipped script."""
        body = _shell_function_body(INGEST_SERVICE_RUN.read_text(),
                                    "run_processor_with_timeout")
        lines = body.splitlines()
        start = next(i for i, l in enumerate(lines)
                     if 'safety_timeout" -gt 0' in l)
        end = next(i for i in range(start, len(lines))
                   if lines[i].strip() == "fi")
        block = "\n".join(lines[start:end + 1]).replace("local ", "")
        script = f'safety_timeout={safety_timeout}\n{block}\necho "${{CWA_CONVERSION_DEADLINE_SECONDS:-unset}}"'
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def test_deadline_is_monotonic_in_the_hard_timeout(self):
        previous = -1
        for safety_timeout in (2, 5, 10, 29, 30, 31, 35, 45, 60, 120, 300, 900, 2700):
            value = int(self._derive(safety_timeout))
            assert value >= previous, (
                f"raising the hard timeout to {safety_timeout}s SHRANK the "
                f"conversion deadline to {value}s (was {previous}s) — a cliff"
            )
            previous = value

    @pytest.mark.parametrize("safety_timeout", [30, 31, 45, 60, 120, 900, 2700])
    def test_conversion_always_gets_the_majority_of_the_budget(self, safety_timeout):
        """The reserve is for backing up and importing the original; it must
        never grow to swallow the conversion window it is protecting."""
        value = int(self._derive(safety_timeout))
        assert value > safety_timeout / 2, (
            f"a {safety_timeout}s timeout left only {value}s to convert"
        )

    @pytest.mark.parametrize("safety_timeout,expected", [(900, 810), (2700, 2430)])
    def test_reachable_timeouts_are_unchanged(self, safety_timeout, expected):
        """ingest_timeout_minutes is clamped to 5..120 server-side, so the
        shipped range is 900..21600s. Pinning the endpoints of what users
        actually run keeps the arithmetic repair from moving live behaviour."""
        assert int(self._derive(safety_timeout)) == expected

    def test_no_hard_limit_means_no_deadline(self):
        assert self._derive(0) == "unset"


class TestKepubFailureAlwaysReturnsAPair:
    """main() unpacks convert_to_kepub() into two names. A path that returned
    None raised TypeError, skipped the fallback and dropped the book."""

    def _kepub_processor(self, tmp_path):
        nbp = object.__new__(ingest_processor.NewBookProcessor)
        nbp.filepath = str(tmp_path / "Book.epub")
        Path(nbp.filepath).write_bytes(b"epub bytes")
        nbp.filename = "Book.epub"
        nbp.input_format = "epub"
        nbp.target_format = "kepub"
        nbp.tmp_conversion_dir = str(tmp_path) + os.sep
        nbp.calibre_env = os.environ.copy()
        nbp.cwa_settings = {"auto_backup_conversions": False}
        nbp.backed_up = []
        nbp.backup = lambda f, backup_type: nbp.backed_up.append((f, backup_type))
        return nbp

    @pytest.mark.parametrize(
        "exc",
        [
            OSError(2, "kepubify missing"),
            RuntimeError("something unexpected"),
            ValueError("bad argument"),
        ],
    )
    def test_unexpected_error_returns_false_pair(
        self, monkeypatch, tmp_path, exc
    ):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(exc)
        )
        nbp = self._kepub_processor(tmp_path)
        result = nbp.convert_to_kepub()

        assert result is not None, "returning None makes main() raise TypeError"
        assert result == (False, "")
        first, second = result  # must be unpackable, as main() does

    def test_non_epub_input_backs_up_the_original_too(
        self, monkeypatch, tmp_path
    ):
        """Backing up only the intermediate left the user's own file nowhere."""
        nbp = self._kepub_processor(tmp_path)
        nbp.filepath = str(tmp_path / "Book.mobi")
        Path(nbp.filepath).write_bytes(b"mobi bytes")
        nbp.filename = "Book.mobi"
        nbp.input_format = "mobi"
        intermediate = str(tmp_path / "Book.epub")
        Path(intermediate).write_bytes(b"epub bytes")

        monkeypatch.setattr(
            ingest_processor.NewBookProcessor,
            "convert_book",
            lambda self, end_format=None: (True, intermediate),
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, "kepubify")
            ),
        )

        assert nbp.convert_to_kepub() == (False, "")
        backed = [f for f, kind in nbp.backed_up if kind == "failed"]
        assert str(nbp.filepath) in backed, (
            "the original the user dropped in must reach the failed folder"
        )
        assert intermediate in backed


class TestNotABookFormatsAreNotRescued:
    """An .acsm is an Adobe fulfillment ticket, not a book. Rescuing it would
    file a junk library entry AND contradict the guidance CWA already prints,
    which promises the file went to processed_books/failed (fork #448)."""

    def test_acsm_conversion_failure_does_not_import_the_ticket(
        self, monkeypatch, tmp_path
    ):
        source = tmp_path / "fulfillment.acsm"
        source.write_text("<fulfillmentToken/>")
        holder = {}

        def _factory(filepath):
            fake = _FakeProcessor(filepath, convert_result=(False, ""))
            fake.input_format = "acsm"
            holder["fake"] = fake
            return fake

        monkeypatch.setattr(ingest_processor, "NewBookProcessor", _factory)
        monkeypatch.setattr(
            ingest_processor, "_acquire_process_lock_or_exit", lambda: None
        )
        assert ingest_processor.main(str(source)) == 0
        assert holder["fake"].imported == [], (
            "an .acsm ticket must not be filed as a book when it fails to convert"
        )

    def test_real_book_formats_are_rescuable(self):
        for fmt in ("pdf", "mobi", "azw3", "PDF", "djvu", None, ""):
            assert ingest_processor.is_rescuable_on_conversion_failure(fmt) is True

    def test_acsm_is_not_rescuable_any_case(self):
        for fmt in ("acsm", "ACSM", "Acsm"):
            assert ingest_processor.is_rescuable_on_conversion_failure(fmt) is False

    def test_every_not_a_book_format_explains_itself_to_the_user(self):
        """Keeps the two registries in sync: silently declining to import a
        file the user dropped is only acceptable if we tell them why."""
        for fmt in ingest_processor._NOT_A_BOOK_FORMATS:
            guidance = ingest_processor.conversion_failure_guidance(
                fmt, f"x.{fmt}"
            )
            assert guidance, f"{fmt} is skipped on failure but explains nothing"
            assert "processed_books/failed" in guidance


class TestFailedBackupIsDiscoverable:
    def test_backup_logs_the_absolute_destination(self, tmp_path, capsys):
        """#1094: 'Moving ... to failed backup' named no path the user could find."""
        failed_dir = tmp_path / "processed_books" / "failed"
        failed_dir.mkdir(parents=True)
        monkey_dest = {"failed": str(failed_dir)}

        source = tmp_path / "Big Handbook.pdf"
        source.write_bytes(b"data")

        nbp = object.__new__(ingest_processor.NewBookProcessor)
        original = ingest_processor.backup_destinations
        try:
            ingest_processor.backup_destinations = monkey_dest
            nbp.backup(str(source), backup_type="failed")
        finally:
            ingest_processor.backup_destinations = original

        out = capsys.readouterr().out
        assert str(failed_dir) in out, (
            "backup() must name the absolute destination directory so the "
            f"user can find the file; got: {out!r}"
        )
        assert (failed_dir / "Big Handbook.pdf").exists()


class TestIngestServiceTimeoutMessaging:
    def test_no_false_internal_timeout_claim(self):
        """There is no internal conversion timeout to have 'not fired'."""
        text = INGEST_SERVICE_RUN.read_text()
        assert "should have timed out internally" not in text, (
            "ingest_timeout_minutes bounds only is_file_in_use(); claiming the "
            "processor has an internal conversion timeout misdirects anyone "
            "debugging a slow conversion"
        )

    def test_safety_timeout_names_the_failed_backup_path(self):
        text = INGEST_SERVICE_RUN.read_text()
        idx = text.find("SAFETY TIMEOUT:")
        assert idx != -1
        window = text[idx : idx + 1600]
        assert "/config/processed_books/failed" in window, (
            "the safety-timeout branch must tell the user where the file went"
        )

    def test_internal_timeout_only_bounds_file_stability_wait(self):
        """Guards the premise of the two assertions above."""
        source = (REPO_ROOT / "scripts/ingest_processor.py").read_text()
        # ingest_timeout_minutes must not reach the conversion subprocess.
        convert_start = source.find("def convert_book")
        convert_end = source.find("def convert_to_kepub")
        assert 0 < convert_start < convert_end
        assert "ingest_timeout_minutes" not in source[convert_start:convert_end]
