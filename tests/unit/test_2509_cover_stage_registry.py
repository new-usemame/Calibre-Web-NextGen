# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2509: finding orphan cover stages must not walk the library before the server listens.

A 129,000-book library on ZFS spent 36 minutes in the startup scavenger's
os.walk before gevent opened port 8083. Startup now visits only the stages
that were registered when they were created; stages left by an earlier
version get one background sweep that never touches a live write.
"""

import io
import os
import time

from PIL import Image
import pytest
from werkzeug.datastructures import FileStorage


pytestmark = pytest.mark.unit


def _jpeg():
    output = io.BytesIO()
    Image.new("RGB", (8, 11), (21, 84, 160)).save(output, format="JPEG")
    return output.getvalue()


def _upload():
    return FileStorage(stream=io.BytesIO(_jpeg()), filename="cover.jpg", content_type="image/jpeg")


@pytest.fixture
def layout(tmp_path, monkeypatch):
    from cps import helper

    library = tmp_path / "library"
    temp_dir = tmp_path / "temp"
    config_dir = tmp_path / "config"
    book_dir = library / "Author" / "Book (7)"
    for path in (book_dir, temp_dir, config_dir):
        path.mkdir(parents=True)
    (book_dir / "cover.jpg").write_bytes(b"old cover")
    monkeypatch.setattr(helper.constants, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(temp_dir))
    return helper, library, book_dir, temp_dir


def _interrupted_stage(helper, book_dir):
    """A real stage whose process died before publish or discard ran."""
    handle, error = helper.save_cover_from_filestorage(str(book_dir), "cover.jpg", _upload())
    assert error is None
    return handle.staged_path


def test_startup_scavenge_removes_registered_stage_without_walking_library(layout, monkeypatch):
    helper, _library, book_dir, _temp = layout
    crashed = _interrupted_stage(helper, book_dir)
    legacy = book_dir / ".cover.jpg.cwng-legacy.stage"
    legacy.write_bytes(b"from an older version")

    def no_walk(*_args, **_kwargs):
        raise AssertionError("startup scavenging walked a directory tree")
    monkeypatch.setattr(helper.os, "walk", no_walk)

    assert helper.scavenge_staged_cover_files() == 1
    assert not os.path.exists(crashed)
    assert legacy.exists()  # left for the background sweep
    assert (book_dir / "cover.jpg").read_bytes() == b"old cover"
    assert os.listdir(helper._cover_stage_registry_dir()) == []


def test_published_and_discarded_stages_leave_nothing_to_scavenge(layout):
    helper, _library, book_dir, _temp = layout
    published, _ = helper.save_cover_from_filestorage(str(book_dir), "cover.jpg", _upload())
    discarded, _ = helper.save_cover_from_filestorage(str(book_dir), "cover.jpg", _upload())
    assert len(os.listdir(helper._cover_stage_registry_dir())) == 2

    assert published.publish() == (True, None)
    assert discarded.discard() == (True, None)

    assert os.listdir(helper._cover_stage_registry_dir()) == []
    assert helper.scavenge_staged_cover_files() == 0
    assert (book_dir / "cover.jpg").read_bytes() == _jpeg_from(published)


def _jpeg_from(handle):
    with open(handle.target_path, "rb") as cover:
        return cover.read()


def test_legacy_sweep_spares_live_writes_and_runs_once(layout, monkeypatch):
    helper, library, book_dir, temp_dir = layout
    old = time.time() - 3600
    legacy = book_dir / ".cover.jpg.cwng-legacy.stage"
    legacy_drive = temp_dir / ".cover.jpg.cwng-drive.stage"
    unrelated = book_dir / ".cover.jpg.other.stage"
    for path in (legacy, legacy_drive, unrelated):
        path.write_bytes(b"staged")
        os.utime(path, (old, old))
    started_before = time.time()
    live_registered = _interrupted_stage(helper, book_dir)
    os.utime(live_registered, (old, old))  # registered: a live write, whatever its age
    live_unregistered = book_dir / ".cover.jpg.cwng-newer.stage"
    live_unregistered.write_bytes(b"written after startup")

    assert helper.sweep_unregistered_cover_stages(started_before) == 2
    assert not legacy.exists() and not legacy_drive.exists()
    assert unrelated.exists()
    assert os.path.exists(live_registered)
    assert live_unregistered.exists()
    assert (book_dir / "cover.jpg").read_bytes() == b"old cover"

    def no_walk(*_args, **_kwargs):
        raise AssertionError("the one-time sweep ran again")
    monkeypatch.setattr(helper.os, "walk", no_walk)
    assert helper.sweep_unregistered_cover_stages(time.time()) == 0
    assert helper.start_legacy_cover_stage_sweep() is None


def test_startup_runs_the_legacy_sweep_off_the_calling_thread(layout, monkeypatch):
    helper, _library, book_dir, _temp = layout
    legacy = book_dir / ".cover.jpg.cwng-legacy.stage"
    legacy.write_bytes(b"staged")
    old = time.time() - 3600
    os.utime(legacy, (old, old))

    worker = helper.start_legacy_cover_stage_sweep()
    assert worker is not None and worker.daemon
    worker.join(10)
    assert not worker.is_alive()
    assert not legacy.exists()
