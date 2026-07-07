# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #103 (mirrors janeczku/calibre-web#3274).

Pre-fix: `do_download_file()` did `download_name + "." + book_format` in
five places without validating that `data.name` (which becomes
`book_name` and then `download_name` in three of the four code branches)
or `book_format` is a usable string. If a calibre.db `data` row had a
NULL `name`, or a caller passed `book_format=None`, the function fell
into `None + "."` deep inside and raised an unhandled
`TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'` —
which Flask surfaces as a 500.

Post-fix: a precondition guard at the top of the function rejects
unusable inputs with `abort(400)` and a diagnostic log line that names
the offending book id and format. Easier to debug, no more 500s.

Root-cause-first per the operator's standing rule: this is not a
try/except mask — it pinpoints the corrupt row + bad caller + cleanly
short-circuits at the boundary instead of letting the bad value
propagate through the function body.
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.exceptions import BadRequest


@pytest.mark.unit
class TestDoDownloadFileInputValidation:
    """Pin the `abort(400)` guard at the top of `do_download_file`."""

    def _call(self, *, book_id=42, data_name="Book Title", book_format="epub"):
        """Invoke do_download_file with controllable inputs. Patches out
        every external dependency the function reaches for so the guard
        runs in isolation and we don't need a Flask app context."""
        from cps import helper as helper_mod
        book = MagicMock()
        book.id = book_id
        book.path = "Author/Title (42)"
        data = MagicMock()
        data.name = data_name
        client = "browser"
        headers = MagicMock()
        # The function calls `abort(400)` from werkzeug which raises
        # BadRequest in pytest contexts (no Flask error handler stack).
        with patch.object(helper_mod, "config", MagicMock(
                config_use_google_drive=False, config_calibre_dir="/calibre",
                config_kepubifypath="", config_binariesdir="",
                config_embed_metadata=False, get_book_path=lambda: "/books")):
            return helper_mod.do_download_file(book, book_format, client, data, headers)

    def test_data_with_none_name_aborts_400(self):
        """If data.name is None (anomalous calibre row), surface a 400
        with a diagnostic log line instead of crashing with a 500."""
        with pytest.raises(BadRequest):
            self._call(data_name=None)

    def test_data_with_empty_name_aborts_400(self):
        """Empty string is just as broken as None — `"" + "." + format`
        produces `".epub"` which is a usable filename for `os.path.join`
        but a useless download. Reject explicitly."""
        with pytest.raises(BadRequest):
            self._call(data_name="")

    def test_book_format_none_aborts_400(self):
        """The reported #3274 traceback bottoms out in `download_name + "."
        + book_format`. If book_format itself is None, the same TypeError
        fires. Pre-validate."""
        with pytest.raises(BadRequest):
            self._call(book_format=None)

    def test_book_format_empty_aborts_400(self):
        with pytest.raises(BadRequest):
            self._call(book_format="")

    def test_book_format_non_string_aborts_400(self):
        """Defensive: book_format must be a real string. A caller
        passing `int` or `bytes` is a programming error we'd rather
        catch at the boundary than have it crash the file ops."""
        with pytest.raises(BadRequest):
            self._call(book_format=123)

    def test_data_is_None_aborts_400(self):
        """The function takes `data` as an argument; None is a bad call."""
        from cps import helper as helper_mod
        book = MagicMock()
        book.id = 7
        with patch.object(helper_mod, "config", MagicMock(config_use_google_drive=False)):
            with pytest.raises(BadRequest):
                helper_mod.do_download_file(book, "epub", "browser", None, MagicMock())

    def test_valid_inputs_dont_abort_in_the_guard(self):
        """Sanity: with a valid data.name + book_format, the precondition
        guard does NOT fire. The function may still fail on later filesystem
        ops (we mock most of those out), but it must not raise BadRequest
        from the guard.

        We patch out the filesystem-touching parts and assert the call
        gets past the guard (i.e. ANY exception other than BadRequest, or
        the call returning, is acceptable — we only pin the absence of
        the boundary 400 here)."""
        from cps import helper as helper_mod
        book = MagicMock()
        book.id = 42
        book.path = "Author/Title (42)"
        data = MagicMock()
        data.name = "Real Filename"
        # The post-send checksum-registration block (#633) calls
        # is_koreader_sync_enabled(), which opens the CWA settings DB. That
        # path is orthogonal to the input guard under test and, in the test
        # env, hits a read-only /config and hard-exits — so mock it out to
        # keep this test focused on the boundary-400 assertion.
        with patch.object(helper_mod, "config", MagicMock(
                config_use_google_drive=False, config_calibre_dir="/calibre",
                config_kepubifypath="", config_binariesdir="",
                config_embed_metadata=False, get_book_path=lambda: "/nonexistent")), \
            patch.object(helper_mod, "log", MagicMock()), \
            patch("cps.progress_syncing.settings.is_koreader_sync_enabled",
                  MagicMock(return_value=False)), \
            patch.object(helper_mod, "send_from_directory", MagicMock()), \
            patch.object(helper_mod, "make_response", MagicMock()):
            try:
                helper_mod.do_download_file(book, "epub", "browser", data, {})
            except BadRequest:
                pytest.fail(
                    "valid inputs must not trip the precondition guard — "
                    "got BadRequest, expected pass-through to the function body"
                )
            except Exception:
                # Other downstream errors (file-not-found, etc.) are fine —
                # they prove we got past the guard.
                pass


@pytest.mark.unit
class TestDoDownloadFileSourcePin:
    """Source-level pin so a future refactor can't quietly drop the
    precondition guard. The guard's existence is what prevents the 500."""

    def test_function_has_input_guard_at_top(self):
        """Pin: the first action in `do_download_file` must be input
        validation. If a future edit moves it below, the function regresses
        to the original NoneType-crash behavior."""
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent.parent
        src = (repo_root / "cps" / "helper.py").read_text()
        import re
        match = re.search(
            r"def do_download_file\(.*?\n(.*?)(?=\n(?:def |@)\w)",
            src, re.DOTALL,
        )
        assert match is not None, "do_download_file body not found"
        body = match.group(1)
        # Strip comments + docstrings to compare against actual code only —
        # the function header has a comment that mentions `None + "."` etc.
        code_lines = []
        for line in body.split("\n"):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        # First STRING-CONCATENATION operation in the function body must
        # appear AFTER the abort(400) guard.
        # Use a simple marker: the first `+ "."` in code-only.
        first_concat_pos = code_only.find('+ "."')
        guard_section = code_only[:first_concat_pos] if first_concat_pos > 0 else code_only
        assert "abort(400)" in guard_section, (
            "do_download_file must abort(400) on invalid inputs BEFORE "
            "any string concatenation that could trip on None — see fork #103"
        )
        # Pin both validations are present
        assert "book_format" in guard_section
        assert "name" in guard_section, (
            "guard must check `data.name` (or `book_name`) is non-empty"
        )
