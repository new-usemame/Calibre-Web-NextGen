# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from pathlib import Path

import pytest

from cps import duplicate_index


pytestmark = pytest.mark.unit


@pytest.fixture
def ingest_processor(monkeypatch):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    import ingest_processor

    return ingest_processor


def _reader_marker_paths(monkeypatch):
    checked_paths = []
    monkeypatch.setattr(
        duplicate_index.os.path,
        "exists",
        lambda path: checked_paths.append(path) or False,
    )
    assert duplicate_index.ingest_batch_follow_up_pending() is False
    active_path, dirty_path, running_path = checked_paths
    assert running_path == f"{dirty_path}.running"
    return dirty_path, active_path


def test_marker_env_knobs_reach_reader(monkeypatch, tmp_path):
    dirty_file = tmp_path / "custom_ingest_batch_dirty"
    active_file = tmp_path / "custom_ingest_batch_active"
    monkeypatch.setenv("CWA_INGEST_BATCH_DIRTY_FILE", str(dirty_file))
    monkeypatch.setenv("CWA_INGEST_BATCH_ACTIVE_FILE", str(active_file))

    assert _reader_marker_paths(monkeypatch) == (
        str(dirty_file),
        str(active_file),
    )


def test_active_ingest_marks_follow_up_pending(monkeypatch, tmp_path):
    active_file = tmp_path / "custom_ingest_batch_active"
    monkeypatch.setenv("CWA_INGEST_BATCH_ACTIVE_FILE", str(active_file))
    active_file.write_text("active_at=1\n")

    assert duplicate_index.ingest_batch_follow_up_pending() is True


def test_marker_config_dir_defaults_agree_across_both_halves(
    ingest_processor, monkeypatch, tmp_path
):
    expected_dirty = str(tmp_path / "cwa_ingest_batch_dirty")
    expected_active = str(tmp_path / "cwa_ingest_batch_active")
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path))
    monkeypatch.delenv("CWA_INGEST_BATCH_DIRTY_FILE", raising=False)
    monkeypatch.delenv("CWA_INGEST_BATCH_ACTIVE_FILE", raising=False)
    monkeypatch.setattr(duplicate_index.constants, "CONFIG_DIR", str(tmp_path))

    reader_paths = _reader_marker_paths(monkeypatch)
    writer_paths = (
        ingest_processor.get_ingest_batch_dirty_file(),
        ingest_processor.get_ingest_batch_active_file(),
    )
    assert reader_paths == writer_paths == (expected_dirty, expected_active)

    monkeypatch.setenv("CWA_INGEST_BATCH_DIRTY_FILE", "")
    monkeypatch.setenv("CWA_INGEST_BATCH_ACTIVE_FILE", "")
    assert _reader_marker_paths(monkeypatch) == writer_paths == (
        ingest_processor.get_ingest_batch_dirty_file(),
        ingest_processor.get_ingest_batch_active_file(),
    )


def test_docker_marker_paths_remain_byte_identical(
    ingest_processor, monkeypatch
):
    monkeypatch.setenv("CALIBRE_DBPATH", "/config")
    monkeypatch.delenv("CWA_INGEST_BATCH_DIRTY_FILE", raising=False)
    monkeypatch.delenv("CWA_INGEST_BATCH_ACTIVE_FILE", raising=False)
    monkeypatch.setattr(duplicate_index.constants, "CONFIG_DIR", "/config")

    reader_paths = _reader_marker_paths(monkeypatch)
    writer_paths = (
        ingest_processor.get_ingest_batch_dirty_file(),
        ingest_processor.get_ingest_batch_active_file(),
    )
    assert reader_paths == writer_paths == (
        "/config/cwa_ingest_batch_dirty",
        "/config/cwa_ingest_batch_active",
    )
