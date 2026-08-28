# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fork #984: fulfilment tickets must run their converter even when ordinary
book conversion is disabled.

An ACSM or LCPL file is a ticket/licence, not a book that can be imported in
its original format.  Auto-Convert OFF and the per-format conversion ignore
list therefore cannot use the ordinary "import the original" fallback: the
plugin-backed converter is the only path that can produce a book.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest_processor  # noqa: E402

_REAL_CONVERT_BOOK = ingest_processor.NewBookProcessor.convert_book


class _FakeProcessor:
    def __init__(
        self,
        filepath,
        *,
        input_format,
        auto_convert_on,
        convert_result,
        target_format="epub",
        convert_ignored_formats=(),
        convert_retained_formats=(),
        can_convert=True,
        last_added_book_id=None,
        use_real_converter=False,
    ):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.input_format = input_format
        self.target_format = target_format
        self.tmp_conversion_dir = os.path.join(
            os.path.dirname(filepath), "tmp_conversion"
        ) + os.sep
        self.ingest_ignored_formats = []
        self.convert_ignored_formats = list(convert_ignored_formats)
        self.convert_retained_formats = list(convert_retained_formats)
        self.cwa_settings = {
            "ingest_timeout_minutes": 15,
            "auto_backup_conversions": False,
        }
        self.calibre_env = os.environ.copy()
        self.is_target_format = False
        self.auto_convert_on = auto_convert_on
        self.can_convert = can_convert
        self.last_added_book_id = last_added_book_id
        self._convert_result = convert_result
        self._use_real_converter = use_real_converter
        self.imported = []
        self.convert_book_calls = []
        self.convert_to_kepub_calls = 0
        self.backed_up = []
        self.added_formats = []

    def is_file_in_use(self, timeout=None):
        return True

    def is_supported_audiobook(self):
        return False

    def convert_book(self, end_format=None):
        self.convert_book_calls.append(end_format)
        if self._use_real_converter:
            return _REAL_CONVERT_BOOK(self, end_format=end_format)
        return self._convert_result

    def convert_to_kepub(self):
        self.convert_to_kepub_calls += 1
        return self._convert_result

    def add_book_to_library(self, filepath, *args, **kwargs):
        self.imported.append(filepath)

    def add_format_to_book(self, book_id, filepath):
        self.added_formats.append((book_id, filepath))

    def backup(self, filepath, backup_type):
        self.backed_up.append((filepath, backup_type))
        return True

    def set_library_permissions(self):
        pass

    def delete_current_file(self):
        pass


def _run_main(
    monkeypatch,
    tmp_path,
    *,
    input_format,
    auto_convert_on,
    convert_result,
    target_format="epub",
    convert_ignored_formats=(),
    convert_retained_formats=(),
    can_convert=True,
    last_added_book_id=None,
    create_import_manifest=False,
    use_real_converter=False,
):
    source = tmp_path / f"Library Ticket.{input_format}"
    source.write_text("ticket or book contents")
    if create_import_manifest:
        Path(str(source) + ".cwa.json").write_text('{"action": "import"}')
    holder = {}

    def _factory(filepath):
        fake = _FakeProcessor(
            filepath,
            input_format=input_format,
            auto_convert_on=auto_convert_on,
            convert_result=convert_result,
            target_format=target_format,
            convert_ignored_formats=convert_ignored_formats,
            convert_retained_formats=convert_retained_formats,
            can_convert=can_convert,
            last_added_book_id=last_added_book_id,
            use_real_converter=use_real_converter,
        )
        holder["fake"] = fake
        return fake

    monkeypatch.setattr(ingest_processor, "NewBookProcessor", _factory)
    monkeypatch.setattr(ingest_processor, "initialize_runtime", lambda: True)
    monkeypatch.setattr(
        ingest_processor, "_acquire_process_lock_or_exit", lambda: None
    )

    assert ingest_processor.main(str(source)) == 0
    return holder["fake"], str(source)


@pytest.mark.parametrize("ticket_format", ["acsm", "lcpl"])
def test_ticket_is_fulfilled_when_auto_convert_is_off(
    monkeypatch, tmp_path, capsys, ticket_format
):
    converted = str(tmp_path / f"Library Ticket.{ticket_format}.epub")
    fake, source = _run_main(
        monkeypatch,
        tmp_path,
        input_format=ticket_format,
        auto_convert_on=False,
        convert_result=(True, converted),
    )

    output = capsys.readouterr().out
    assert fake.convert_book_calls == [None]
    assert fake.imported == [converted]
    assert source not in fake.imported
    assert "fulfilment ticket" in output
    assert "Auto-Convert is deactivated" in output


@pytest.mark.parametrize("ticket_format", ["acsm", "lcpl"])
def test_ticket_is_fulfilled_even_when_its_format_is_ignored(
    monkeypatch, tmp_path, capsys, ticket_format
):
    converted = str(tmp_path / f"Library Ticket.{ticket_format}.epub")
    fake, source = _run_main(
        monkeypatch,
        tmp_path,
        input_format=ticket_format,
        auto_convert_on=True,
        convert_result=(True, converted),
        convert_ignored_formats=(ticket_format,),
    )

    assert fake.convert_book_calls == [None]
    assert fake.imported == [converted]
    assert source not in fake.imported
    assert "Auto-Convert ignore list" in capsys.readouterr().out


def test_real_book_is_still_imported_as_is_when_auto_convert_is_off(
    monkeypatch, tmp_path
):
    fake, source = _run_main(
        monkeypatch,
        tmp_path,
        input_format="pdf",
        auto_convert_on=False,
        convert_result=(True, str(tmp_path / "converted.epub")),
    )

    assert fake.convert_book_calls == []
    assert fake.convert_to_kepub_calls == 0
    assert fake.imported == [source]


def test_unconvertible_acsm_uses_ticket_failure_flow(
    monkeypatch, tmp_path, capsys
):
    fake, source = _run_main(
        monkeypatch,
        tmp_path,
        input_format="acsm",
        auto_convert_on=False,
        convert_result=(False, ""),
        can_convert=False,
        create_import_manifest=True,
    )

    output = capsys.readouterr().out
    assert fake.convert_book_calls == []
    assert fake.imported == []
    assert fake.backed_up == [(source, "failed")]
    assert not Path(source + ".cwa.json").exists()
    assert "ACSM_NOTICE:" in output
    assert "is currently unsupported / is not a known ebook format" not in output


def test_successful_fulfilment_never_retains_raw_ticket_as_book_format(
    monkeypatch, tmp_path
):
    converted = str(tmp_path / "Library Ticket.epub")
    fake, source = _run_main(
        monkeypatch,
        tmp_path,
        input_format="acsm",
        auto_convert_on=True,
        convert_result=(True, converted),
        convert_retained_formats=("acsm",),
        last_added_book_id=7,
    )

    assert fake.imported == [converted]
    assert fake.added_formats == []
    assert source not in fake.imported


def test_failed_ticket_fulfilment_keeps_plugin_reason_and_preserves_original(
    monkeypatch, tmp_path, capsys
):
    source_path = tmp_path / "Library Ticket.acsm"
    import_manifest = Path(str(source_path) + ".cwa.json")
    import_manifest.write_text('{"action": "import"}')
    failed_manifest = Path(str(source_path) + ".cwa.failed.json")
    failed_manifest.write_text("preserve this")
    plugin_output = (
        "ValueError: No plugin to handle input format: acsm\n"
        "DeACSM v0.0.16: Trying to parse file Library Ticket.acsm\n"
        "DeACSM v0.0.16: ADE auth is missing or broken\n"
    )

    def _fail(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, output=plugin_output)

    monkeypatch.setattr(ingest_processor, "_run_converter_streaming", _fail)
    fake, source = _run_main(
        monkeypatch,
        tmp_path,
        input_format="acsm",
        auto_convert_on=False,
        convert_result=(False, ""),
        use_real_converter=True,
    )

    output = capsys.readouterr().out
    assert fake.convert_book_calls == [None]
    assert fake.imported == []
    assert fake.backed_up == [(source, "failed")]
    assert not import_manifest.exists()
    assert failed_manifest.read_text() == "preserve this"
    assert "ADE auth is missing or broken" in output
    assert "An ACSM-capable Calibre plugin is installed and did run" in output
    assert "place the ACSM Input plugin zip" not in output


def test_acsm_targeting_kepub_uses_the_two_stage_fulfilment_route(
    monkeypatch, tmp_path
):
    converted = str(tmp_path / "Library Ticket.kepub")
    fake, source = _run_main(
        monkeypatch,
        tmp_path,
        input_format="acsm",
        auto_convert_on=False,
        convert_result=(True, converted),
        target_format="kepub",
    )

    assert fake.convert_to_kepub_calls == 1
    assert fake.convert_book_calls == []
    assert fake.imported == [converted]
    assert source not in fake.imported
