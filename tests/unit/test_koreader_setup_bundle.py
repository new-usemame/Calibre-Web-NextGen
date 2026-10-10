# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ready-made KOReader plugin download, end to end.

A signed-in person downloads the plugin through the real
``/api/v1/devices/koreader/setup-bundle`` route; the zip is opened the way a
computer would, and the sign-in inside it is tried on the real KOReader
sign-in, before and after the person revokes it on the account page. The
plugin archive is packed from this checkout's plugin folder the way the image
build packs ``static/koplugin.zip``.
"""

import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from cps import ub
from cps.services import koreader_bundle
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

BUNDLE = "/api/v1/devices/koreader/setup-bundle"
PLUGIN_SOURCE = Path(__file__).resolve().parents[2] / "koreader" / "plugins" / "cwngsync.koplugin"


def built_plugin_archive(path, entries=None):
    """``static/koplugin.zip`` as the image builds it (``zip -r`` of the
    plugin folder), or with ``entries`` {name: text} when given."""
    with zipfile.ZipFile(path, "w") as archive:
        if entries is not None:
            for name, text in entries.items():
                archive.writestr(name, text)
        else:
            for file in sorted(PLUGIN_SOURCE.rglob("*")):
                if file.is_file():
                    archive.write(file, "cwngsync.koplugin/%s"
                                  % file.relative_to(PLUGIN_SOURCE).as_posix())
    return path


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    w.enable_web()
    w.add_user("alice", password="alice-account-password")
    archive = built_plugin_archive(tmp_path / "koplugin.zip")
    monkeypatch.setattr(koreader_bundle, "_static_archive", lambda: str(archive))
    yield w
    w.close()


def unzip(response):
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    names = [info.filename for info in archive.infolist()]
    # A repeated name extracts differently from one unzip tool to the next.
    assert len(names) == len(set(names)), names
    return {info.filename: archive.read(info) for info in archive.infolist()}


def signs_in(world, username, password):
    return world.client.get("/kosync/users/auth",
                            headers=world.basic(username, password)).status_code == 200


def test_the_ready_made_plugin_signs_in_until_it_is_revoked(world):
    alice = world.browser("alice")
    download = alice.post(BUNDLE)
    assert download.status_code == 200
    assert download.headers["Content-Type"] == "application/zip"
    assert download.headers["Content-Disposition"] == (
        'attachment; filename="cwngsync-ready-made.zip"')
    assert "no-store" in download.headers["Cache-Control"]

    files = unzip(download)
    # One folder, named as KOReader expects, holding the real plugin.
    assert {name.split("/")[0] for name in files} == {"cwngsync.koplugin"}
    assert b"cwngsync" in files["cwngsync.koplugin/_meta.lua"].lower()
    assert "cwngsync.koplugin/main.lua" in files
    setup = json.loads(files["cwngsync.koplugin/setup.json"])
    assert set(setup) == {"server", "username", "password", "created_at"}
    assert setup["server"] == "http://localhost"
    assert setup["username"] == "alice"
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", setup["created_at"])
    assert signs_in(world, "alice", setup["password"])

    row = world.session.query(ub.UserAppPassword).one()
    assert row.label == "KOReader (ready-made download %s)" % setup["created_at"][:10]
    assert setup["password"] not in (row.password_hash, row.label)
    revoked = alice.post("/api/v1/account/app-passwords/%d/delete" % row.id)
    assert revoked.status_code == 204
    assert not signs_in(world, "alice", setup["password"])


def test_each_download_is_its_own_sign_in(world):
    alice = world.browser("alice")
    first = json.loads(unzip(alice.post(BUNDLE))["cwngsync.koplugin/setup.json"])
    second = json.loads(unzip(alice.post(BUNDLE))["cwngsync.koplugin/setup.json"])
    assert first["password"] != second["password"]
    assert world.session.query(ub.UserAppPassword).count() == 2


def test_the_reader_can_choose_the_address_the_device_will_use(world):
    alice = world.browser("alice")
    for typed, expected in (("192.168.1.20:8083/", "http://192.168.1.20:8083"),
                            ("https://books.example.com/cwa/kosync", "https://books.example.com/cwa"),
                            ("http://[fd00::5]:8083", "http://[fd00::5]:8083")):
        download = alice.post(BUNDLE, json={"server": typed})
        assert download.status_code == 200, typed
        setup = json.loads(unzip(download)["cwngsync.koplugin/setup.json"])
        assert setup["server"] == expected
    before = world.session.query(ub.UserAppPassword).count()
    for bad in ("ftp://books.example.com", "http://alice:pw@books.example.com",
                "books.example.com?next=evil", "http://", "two words", "x" * 300):
        refused = alice.post(BUNDLE, json={"server": bad})
        assert refused.status_code == 400, bad
        assert refused.get_json()["error"]["code"] == "invalid_server"
    assert world.session.query(ub.UserAppPassword).count() == before


def test_the_download_carries_the_plugin_and_nothing_private(world, monkeypatch, tmp_path):
    # What a local image build zips when the plugin folder holds agent or
    # editor state, a stale setup file, or the archive holds another folder.
    archive = built_plugin_archive(tmp_path / "swept-up.zip", {
        "cwngsync.koplugin/_meta.lua": 'return { name = "cwngsync" }',
        "cwngsync.koplugin/main.lua": "-- plugin",
        "cwngsync.koplugin/tests/t_test.lua": "-- test",
        "cwngsync.koplugin/abc123.digest": "digest",
        "cwngsync.koplugin/.claude/agent-memory/notes.md": "private",
        "cwngsync.koplugin/.DS_Store": "finder",
        "cwngsync.koplugin/setup.json": '{"password": "someone else"}',
        "somewhere-else/readme.txt": "not the plugin",
    })
    monkeypatch.setattr(koreader_bundle, "_static_archive", lambda: str(archive))

    files = unzip(world.browser("alice").post(BUNDLE))
    assert sorted(files) == [
        "cwngsync.koplugin/_meta.lua",
        "cwngsync.koplugin/abc123.digest",
        "cwngsync.koplugin/main.lua",
        "cwngsync.koplugin/setup.json",
        "cwngsync.koplugin/tests/t_test.lua",
    ]
    assert b"someone else" not in files["cwngsync.koplugin/setup.json"]


def test_no_plugin_to_ship_means_no_new_password(world, monkeypatch, tmp_path):
    not_the_plugin = built_plugin_archive(tmp_path / "other.zip", {"other/_meta.lua": "x"})
    for archive in (tmp_path / "absent.zip", not_the_plugin):
        monkeypatch.setattr(koreader_bundle, "_static_archive", lambda: str(archive))
        refused = world.browser("alice").post(BUNDLE)
        assert refused.status_code == 503
        assert refused.get_json()["error"]["code"] == "plugin_unavailable"
    assert world.session.query(ub.UserAppPassword).count() == 0


def test_the_download_needs_an_account_and_koreader_sync(world):
    assert world.browser().post(BUNDLE).status_code == 401
    world.sync_switch(False)
    refused = world.browser("alice").post(BUNDLE)
    assert refused.status_code == 409
    assert refused.get_json()["error"]["code"] == "koreader_sync_disabled"
    assert world.session.query(ub.UserAppPassword).count() == 0
