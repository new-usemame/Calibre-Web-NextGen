# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral checks for #1495 folder-label derivation and import writes."""

import importlib.util
import inspect
import json
import sqlite3
import sys
import types
from contextlib import nullcontext
from pathlib import Path

import pytest
import flask
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_calibre_helper(monkeypatch):
    modules = {
        "calibre.db.adding": ["run_import_plugins", "run_import_plugins_before_metadata"],
        "calibre.db.legacy": ["LibraryDatabase"],
        "calibre.db.utils": ["find_identical_books"],
        "calibre.ebooks.metadata": ["string_to_authors"],
        "calibre.ebooks.metadata.meta": ["get_metadata"],
        "calibre.ptempfile": ["TemporaryDirectory"],
    }
    for name, attributes in modules.items():
        module = types.ModuleType(name)
        for attribute in attributes:
            setattr(module, attribute, lambda *args, **kwargs: None)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "_folder_labels_calibre_helper_test", REPO_ROOT / "scripts/calibre_ingest_transaction.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCache:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            "CREATE TABLE identifiers (book INTEGER, type TEXT, val TEXT, UNIQUE(book,type))"
        )
        self.backend = types.SimpleNamespace(execute=self.connection.execute, conn=self.connection)
        self.write_lock = nullcontext()
        self.field_metadata = {
            "identifiers": {"datatype": "text", "is_multiple": {"cache_to_list": ","}},
            "tags": {"datatype": "text", "is_multiple": {"cache_to_list": ","}},
            "#owner": {"datatype": "text", "is_multiple": {"cache_to_list": ","}},
        }
        self.fields = {}
        self.dumped = []

    def field_for(self, field, book_id, default_value=None):
        if field == "identifiers":
            return dict(self.connection.execute(
                "SELECT type,val FROM identifiers WHERE book=?", (book_id,)
            ))
        return self.fields.get((field, book_id), default_value)

    def set_field(self, field, values):
        if field == "identifiers":
            for book_id, identifiers in values.items():
                for key, value in identifiers.items():
                    self.connection.execute(
                        "INSERT OR REPLACE INTO identifiers VALUES (?,?,?)",
                        (book_id, key, value),
                    )
            return
        for book_id, value in values.items():
            self.fields[field, book_id] = list(value)

    def dump_metadata(self, book_ids):
        self.dumped.extend(book_ids)


def test_folder_values_are_root_relative_nested_only_when_requested(tmp_path):
    from cps.services.ingest_folder_labels import folder_label_values

    root = tmp_path / "incoming"
    source = root / "Publisher" / "Series" / "book.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"book")
    root_level = root / "root.epub"
    root_level.write_bytes(b"root book")
    outside = tmp_path / "outside.epub"
    outside.write_bytes(b"outside book")

    assert folder_label_values(source, root) == ["Publisher"]
    assert folder_label_values(source, root, nested=True) == ["Publisher", "Series"]
    assert folder_label_values(root_level, root) == []
    assert folder_label_values(outside, root) is None


def test_folder_values_reject_a_symlink_that_escapes_ingest_root(tmp_path):
    from cps.services.ingest_folder_labels import IngestFolderLabelError, folder_label_values

    root = tmp_path / "incoming"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    source = outside / "book.epub"
    source.write_bytes(b"book")
    alias = root / "Publisher"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    with pytest.raises(IngestFolderLabelError, match="inside"):
        folder_label_values(alias / "book.epub", root)


def test_processor_derives_labels_from_original_watched_source(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    import ingest_processor

    root = tmp_path / "incoming"
    original = root / "Owner" / "Series" / "book.epub"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    converted = tmp_path / "converted.epub"
    converted.write_bytes(b"converted")
    processor = object.__new__(ingest_processor.NewBookProcessor)
    processor.filepath = str(original)
    processor.ingest_folder = str(root)
    processor.cwa_settings = {
        "auto_ingest_folder_label_target": "#owner",
        "auto_ingest_folder_label_nested": True,
    }

    assert processor._folder_label_metadata() == {
        "target": "#owner", "values": ["Owner", "Series"]
    }
    processor.filepath = str(converted)
    assert processor._folder_label_metadata() is None


def test_folder_label_target_accepts_only_existing_multivalue_text_columns():
    from cps.services.ingest_folder_labels import (
        IngestFolderLabelError,
        eligible_custom_column_options,
        validate_target,
    )

    columns = [
        types.SimpleNamespace(label="owner", name="Owner", datatype="text", is_multiple=True),
        types.SimpleNamespace(label="single", name="Single", datatype="text", is_multiple=False),
        types.SimpleNamespace(label="rating", name="Rating", datatype="int", is_multiple=True),
    ]
    options = eligible_custom_column_options(columns)
    assert options == [{"lookup": "#owner", "name": "Owner"}]
    assert validate_target("#owner", options) == "#owner"
    with pytest.raises(IngestFolderLabelError):
        validate_target("#single", options)


def test_original_source_labels_apply_to_new_book_and_marker_replay_unions_values(monkeypatch, tmp_path):
    helper = _load_calibre_helper(monkeypatch)
    cache = FakeCache()
    database = types.SimpleNamespace(new_api=cache, close=lambda: None)
    monkeypatch.setattr(helper, "LibraryDatabase", lambda _path: database)
    staged = tmp_path / "staged.epub"
    staged.write_bytes(b"same book bytes")
    first_source = tmp_path / "ingest" / "Mystery" / "book.epub"
    second_source = tmp_path / "ingest" / "Classics" / "book.epub"
    first_source.parent.mkdir(parents=True)
    second_source.parent.mkdir(parents=True)
    first_source.write_bytes(b"source copy")
    second_source.write_bytes(b"source copy")
    monkeypatch.setattr(
        helper, "prepare_book", lambda path, _overrides: iter([(object(), "epub", path)])
    )
    add_calls = []

    def add(_cache, _metadata, _extension, _path, _automerge, digest):
        add_calls.append(digest)
        _cache.set_field("identifiers", {17: {helper.marker_type(digest): digest}})
        return {17}, set(), {17}

    monkeypatch.setattr(helper, "add_with_automerge", add)

    def args(source, labels):
        return types.SimpleNamespace(
            path=str(staged), identity_path=str(source), expected_import_sha256=None,
            expected_source_sha256=None, database_path=None, library_path=str(tmp_path),
            acquisition=False, metadata_json=json.dumps({"ingest_folder_labels": labels}),
            action="import", automerge="new_record", fail_before_commit=False,
        )

    first = helper.run(args(first_source, {"target": "tags", "values": ["Mystery"]}))
    replay = helper.run(args(second_source, {"target": "tags", "values": ["Classics", "mystery"]}))

    assert first["book_ids"] == [17]
    assert replay["status"] == "already_imported"
    assert add_calls == [helper.content_digest(first_source)]
    assert cache.field_for("tags", 17, default_value=[]) == ["Mystery", "Classics"]
    assert cache.dumped.count(17) == 2


def test_missing_configured_column_fails_before_a_book_is_added(monkeypatch, tmp_path):
    helper = _load_calibre_helper(monkeypatch)
    cache = FakeCache()
    cache.field_metadata.pop("#owner")
    monkeypatch.setattr(
        helper, "LibraryDatabase", lambda _path: types.SimpleNamespace(new_api=cache, close=lambda: None)
    )
    staged = tmp_path / "staged.epub"
    staged.write_bytes(b"book")
    source = tmp_path / "source.epub"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        helper, "prepare_book", lambda path, _overrides: iter([(object(), "epub", path)])
    )
    add_calls = []
    monkeypatch.setattr(helper, "add_with_automerge", lambda *args: add_calls.append(args))
    args = types.SimpleNamespace(
        path=str(staged), identity_path=str(source), expected_import_sha256=None,
        expected_source_sha256=None, database_path=None, library_path=str(tmp_path),
        acquisition=False,
        metadata_json=json.dumps({"ingest_folder_labels": {"target": "#owner", "values": []}}),
        action="import", automerge="new_record", fail_before_commit=False,
    )

    with pytest.raises(ValueError, match="unavailable"):
        helper.run(args)
    assert add_calls == []


def test_classic_partial_saves_preserve_folder_settings_and_validate_target():
    from cps.cwa_functions import _folder_label_settings_for_post

    target_key = "auto_ingest_folder_label_target"
    nested_key = "auto_ingest_folder_label_nested"
    columns = [{"lookup": "#owner", "name": "Owner"}]

    untouched = {target_key: "#owner", nested_key: 1, "other": 4}
    assert not _folder_label_settings_for_post(
        untouched, {"other": "4"}, {target_key: "#owner", nested_key: True}, columns
    )
    assert untouched == {"other": 4}

    target_only = {target_key: "#owner", nested_key: 1}
    assert not _folder_label_settings_for_post(
        target_only, {target_key: "#owner"}, {target_key: "#owner", nested_key: True}, columns
    )
    assert target_only == {target_key: "#owner", nested_key: 0}

    invalid = {target_key: "#missing", nested_key: 1}
    assert _folder_label_settings_for_post(
        invalid,
        {target_key: "#missing", nested_key: "on"},
        {target_key: "#owner", nested_key: False},
        columns,
    )
    assert invalid == {}


def test_admin_settings_api_updates_partial_pair_and_rejects_missing_column():
    from cps.api import ingest_folder_labels as api

    class SettingsDB:
        def __init__(self):
            self.events = []
            self.settings = {
                "auto_ingest_folder_label_target": "#owner",
                "auto_ingest_folder_label_nested": True,
            }
            self.con = types.SimpleNamespace(
                close=lambda: None,
                commit=lambda: self.events.append("commit"),
                execute=lambda *_args: self.events.append("begin"),
            )
            self.cur = self
            self.saved = []

        def get_cwa_settings(self):
            self.events.append("read")
            return dict(self.settings)

        def execute(self, _query, values):
            self.events.append("update")
            self.settings.update({
                "auto_ingest_folder_label_target": values[0],
                "auto_ingest_folder_label_nested": bool(values[1]),
            })
            self.saved.append(tuple(values))

    settings_db = SettingsDB()
    admin = types.SimpleNamespace(
        is_authenticated=True, is_anonymous=False, role_admin=lambda: True,
    )
    options = [{"lookup": "#owner", "name": "Owner"}]
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_request_context(
        "/api/v1/admin/ingest-folder-label-settings", method="PUT", json={"nested": False}
    ), patch.object(api, "current_user", admin), \
            patch.object(api, "_custom_columns", return_value=options), \
            patch.object(api, "_settings_db", return_value=settings_db):
        response = inspect.unwrap(api.update_ingest_folder_label_settings)()
    assert response.status_code == 200
    assert response.get_json()["target"] == "#owner"
    assert response.get_json()["nested"] is False
    assert settings_db.saved == [("#owner", 0)]
    assert settings_db.events[:4] == ["begin", "read", "update", "commit"]

    with app.test_request_context(
        "/api/v1/admin/ingest-folder-label-settings", method="PUT",
        json={"target": "#removed", "nested": True},
    ), patch.object(api, "current_user", admin), \
            patch.object(api, "_custom_columns", return_value=options), \
            patch.object(api, "_settings_db", return_value=settings_db):
        response = inspect.unwrap(api.update_ingest_folder_label_settings)()
    assert response.status_code == 400
    assert settings_db.settings == {
        "auto_ingest_folder_label_target": "#owner",
        "auto_ingest_folder_label_nested": False,
    }
    assert settings_db.saved == [("#owner", 0)]


@pytest.mark.parametrize(
    ("authenticated", "anonymous", "is_admin", "expected"),
    [(False, True, False, 401), (True, False, False, 403)],
)
def test_folder_label_settings_api_requires_an_authenticated_admin(
    authenticated, anonymous, is_admin, expected,
):
    from cps.api import ingest_folder_labels as api

    user = types.SimpleNamespace(
        is_authenticated=authenticated,
        is_anonymous=anonymous,
        role_admin=lambda: is_admin,
    )
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/admin/ingest-folder-label-settings"), \
            patch.object(api, "current_user", user):
        response = api._require_admin()
    assert response.status_code == expected
