"""Regression test for fork #690 (long-filename slice).

When an uploaded format's staging filename exceeds the ingest length cap, the
ingest processor renames it to fit. It used to rename only the book file and
leave the ``<name>.cwa.json`` add_format sidecar behind, so the manifest lookup
missed and the file was imported as a NEW book instead of a format on the
existing one — the reported duplicate. _truncate_overlong_ingest_name now moves
the sidecar alongside.
"""
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def ip(monkeypatch):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    return importlib.import_module("ingest_processor")


def test_short_name_unchanged(ip, tmp_path):
    f = tmp_path / "short.epub"
    f.write_bytes(b"x")
    assert ip._truncate_overlong_ingest_name(str(f), 150) == str(f)
    assert f.exists()


def test_overlong_name_renames_and_moves_manifest(ip, tmp_path):
    long_stem = "L" * 200
    book = tmp_path / (long_stem + ".epub")
    book.write_bytes(b"data")
    manifest = tmp_path / (long_stem + ".epub.cwa.json")
    manifest.write_text('{"action": "add_format", "book_id": 7}')

    new_path = ip._truncate_overlong_ingest_name(str(book), 150)

    # book file was renamed to fit
    assert new_path != str(book)
    assert Path(new_path).exists()
    assert not book.exists()
    assert len(Path(new_path).name) <= 150

    # the sidecar moved alongside so main()'s `<filepath>.cwa.json` lookup hits
    moved_manifest = Path(new_path + ".cwa.json")
    assert moved_manifest.exists(), \
        "add_format sidecar must follow the renamed book file or the upload dups (#690)"
    assert not manifest.exists()
    assert '"add_format"' in moved_manifest.read_text()


def test_overlong_name_without_manifest_is_fine(ip, tmp_path):
    long_stem = "M" * 200
    book = tmp_path / (long_stem + ".epub")
    book.write_bytes(b"data")

    new_path = ip._truncate_overlong_ingest_name(str(book), 150)

    assert Path(new_path).exists()
    assert not Path(new_path + ".cwa.json").exists()  # nothing to move, no crash
