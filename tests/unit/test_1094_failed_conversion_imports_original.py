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
     internally", because no internal conversion timeout exists —
     ``ingest_timeout_minutes`` bounds only the file-stability wait.
"""

import os
import sys
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
