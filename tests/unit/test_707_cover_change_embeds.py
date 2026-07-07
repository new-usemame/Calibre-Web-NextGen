"""Regression test for fork #707 — changing a cover must embed into the book file.

The cover picker updated the stored cover.jpg and stamped last_modified, but did
NOT trigger the metadata/cover enforcer that re-embeds cover.jpg into the actual
book file (via ebook-polish). So downloads (OPDS/Kobo) and the picker's
"Currently embedded" preview kept the old cover. Enforcement is fired by a
{timestamp}-{book_id}.json entry in the change-logs dir — the metadata-*edit*
paths wrote one, the cover picker did not. helper.log_metadata_change is now the
single writer, called from the cover-apply path.
"""
import inspect
import json
import re
from pathlib import Path

import pytest

from cps import helper, cover_picker

pytestmark = pytest.mark.unit


class _FakeAuthor:
    def __init__(self, name):
        self.name = name


class _FakeBook:
    id = 84
    title = "Carl's Doomsday Scenario"
    authors = [_FakeAuthor("Matt Dinniman")]


def test_log_metadata_change_writes_enforcer_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "CWA_METADATA_CHANGE_LOGS_DIR", str(tmp_path))
    out = helper.log_metadata_change(_FakeBook(), {"cover": True})

    assert out is not None
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1, "a cover change must queue exactly one enforcement entry (#707)"

    # filename must be {timestamp}-{book_id}.json so the enforcer can key the book
    name = files[0].name
    assert re.fullmatch(r"\d{14}-84\.json", name), f"bad enforcer log name: {name}"

    payload = json.loads(files[0].read_text())
    assert payload.get("cover") is True
    assert payload["title"] == "Carl's Doomsday Scenario"
    assert "_cwa_meta" in payload  # enforcer metadata block present


def test_log_metadata_change_never_raises(monkeypatch, tmp_path):
    # An unwritable dir must not fail the user's cover change. Use a path whose
    # parent is a regular file, so os.makedirs reliably fails on every platform.
    blocker = tmp_path / "iamafile"
    blocker.write_text("x")
    monkeypatch.setattr(helper, "CWA_METADATA_CHANGE_LOGS_DIR", str(blocker / "sub"))
    # Should swallow the error and return None, not raise.
    assert helper.log_metadata_change(_FakeBook(), {"cover": True}) is None


def test_cover_picker_apply_triggers_enforcement_source_pin():
    src = inspect.getsource(cover_picker)
    assert "log_metadata_change" in src, \
        "cover apply must call helper.log_metadata_change or the cover never embeds (#707)"
