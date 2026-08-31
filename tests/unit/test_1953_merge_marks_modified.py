# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for #1953's merge-format cursor provenance."""

from datetime import datetime, timezone
import inspect
import json
from types import SimpleNamespace

from flask import Flask
import pytest


pytestmark = pytest.mark.unit


class _Data:
    def __init__(self, book, book_format, uncompressed_size, name):
        self.book = book
        self.format = book_format
        self.uncompressed_size = uncompressed_size
        self.name = name


@pytest.mark.parametrize("overwrite", [False, True])
def test_merge_format_mutation_advances_target_last_modified(
    tmp_path, monkeypatch, overwrite,
):
    """Adding or overwriting a format advances Kobo's book cursor."""
    from cps import editbooks

    target_dir = tmp_path / "target"
    source_dir = tmp_path / "source"
    target_dir.mkdir()
    source_dir.mkdir()
    old_modified = datetime(2020, 1, 2, tzinfo=timezone.utc)
    target_format = "EPUB" if overwrite else "KEPUB"
    target_data = [
        SimpleNamespace(
            format="EPUB",
            name="Target - Author",
            uncompressed_size=10,
        ),
    ]
    target = SimpleNamespace(
        id=1,
        title="Target",
        path="target",
        authors=[SimpleNamespace(name="Author")],
        data=target_data,
        last_modified=old_modified,
    )
    source = SimpleNamespace(
        id=2,
        title="Source",
        path="source",
        authors=[SimpleNamespace(name="Author")],
        data=[
            SimpleNamespace(
                format=target_format,
                name="source-format",
                uncompressed_size=20,
            ),
        ],
    )
    (source_dir / f"source-format.{target_format.lower()}").write_bytes(
        b"replacement format bytes",
    )
    if overwrite:
        (target_dir / "Target - Author.epub").write_bytes(
            b"old target bytes",
        )

    commits = []
    dirty_book_ids = []
    books = {1: target, 2: source}
    monkeypatch.setattr(
        editbooks.calibre_db, "get_book", lambda book_id: books.get(book_id),
    )
    monkeypatch.setattr(
        editbooks.calibre_db,
        "session",
        SimpleNamespace(commit=lambda: commits.append(True)),
    )
    monkeypatch.setattr(
        editbooks.calibre_db,
        "set_metadata_dirty",
        lambda book_id: dirty_book_ids.append(book_id),
    )
    monkeypatch.setattr(
        editbooks.config, "get_book_path", lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        editbooks.helper, "get_valid_filename", lambda value, chars=96: value,
    )
    monkeypatch.setattr(editbooks.db, "Data", _Data)
    monkeypatch.setattr(
        editbooks,
        "delete_book_from_table",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        editbooks, "_queue_duplicate_scan_after_change", lambda *_args: None,
    )

    payload = {
        "Merge_books": [target.id, source.id],
        "overwrite_formats": [target_format] if overwrite else [],
    }
    app = Flask(__name__)
    with app.test_request_context("/ajax/mergebooks", json=payload):
        result = inspect.unwrap(editbooks.merge_list_book)()

    assert json.loads(result) == {"success": True}
    assert target.last_modified > old_modified
    assert dirty_book_ids == [target.id]
    assert commits == [True]
    changed = next(row for row in target.data if row.format == target_format)
    assert changed.uncompressed_size == 20


def test_automatic_duplicate_merge_advances_keeper_last_modified(
    tmp_path, monkeypatch,
):
    """Automatic resolution exposes an added KEPUB to Kobo's cursor."""
    from cps import duplicates

    target_dir = tmp_path / "target"
    source_dir = tmp_path / "source"
    target_dir.mkdir()
    source_dir.mkdir()
    old_modified = datetime(2020, 1, 2, tzinfo=timezone.utc)
    target = SimpleNamespace(
        id=1,
        title="Target",
        path="target",
        authors=[SimpleNamespace(name="Author")],
        data=[
            SimpleNamespace(
                format="EPUB",
                name="Target - Author",
                uncompressed_size=10,
            ),
        ],
        last_modified=old_modified,
    )
    source = SimpleNamespace(
        id=2,
        title="Source",
        path="source",
        authors=[SimpleNamespace(name="Author")],
        data=[
            SimpleNamespace(
                format="KEPUB",
                name="source-format",
                uncompressed_size=20,
            ),
        ],
    )
    (source_dir / "source-format.kepub").write_bytes(b"kepub bytes")
    commits = []
    books = {1: target, 2: source}
    monkeypatch.setattr(
        duplicates.calibre_db,
        "get_book",
        lambda book_id: books.get(book_id),
    )
    monkeypatch.setattr(
        duplicates.calibre_db,
        "session",
        SimpleNamespace(
            commit=lambda: commits.append(True),
            rollback=lambda: None,
        ),
    )
    monkeypatch.setattr(
        duplicates.config, "get_book_path", lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        duplicates.helper,
        "get_valid_filename",
        lambda value, chars=96: value,
    )
    monkeypatch.setattr(duplicates.db, "Data", _Data)

    duplicates.merge_duplicate_group(
        SimpleNamespace(id=target.id),
        [SimpleNamespace(id=source.id)],
    )

    assert target.last_modified > old_modified
    assert commits == [True]
    added = next(row for row in target.data if row.format == "KEPUB")
    assert added.uncompressed_size == 20
