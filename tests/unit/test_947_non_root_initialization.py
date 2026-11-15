"""Container first-boot behavior when PID 1 is an arbitrary non-root user."""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from auto_library import AutoLibrary  # noqa: E402

IMAGE_POLICY_HELPER = SCRIPTS_DIR / "configure_image_policy.sh"
RUNTIME_DIRS_HELPER = SCRIPTS_DIR / "ensure_runtime_dirs.sh"


def _auto_library(dirs_path: Path, library_path: Path) -> AutoLibrary:
    instance = AutoLibrary.__new__(AutoLibrary)
    instance.dirs_path = str(dirs_path)
    instance.lib_path = str(library_path)
    return instance


def test_default_discovered_library_does_not_rewrite_immutable_image_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A fresh container should boot when discovery matches its shipped library path.

    The image's dirs.json is read-only for an arbitrary container UID. Rewriting
    it to the same path is unnecessary; the previous implementation failed
    cwa-auto-library and prevented the web service from starting.
    """
    dirs_path = tmp_path / "dirs.json"
    original = b'{\n  "ingest_folder": "/cwa-book-ingest",\n  "calibre_library_dir": "/calibre-library"\n}\n'
    dirs_path.write_bytes(original)
    library = Path("/calibre-library")
    real_open = builtins.open

    def deny_writes(file, mode="r", *args, **kwargs):
        if Path(file) == dirs_path and any(flag in mode for flag in "wax+"):
            raise PermissionError("read-only image configuration")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.delenv("CWA_CALIBRE_LIBRARY_DIR", raising=False)
    monkeypatch.setattr(builtins, "open", deny_writes)

    _auto_library(dirs_path, library).update_dirs_json()

    assert dirs_path.read_bytes() == original


def test_discovered_library_elsewhere_still_persists_its_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    dirs_path = tmp_path / "dirs.json"
    dirs_path.write_text(json.dumps({"calibre_library_dir": "/calibre-library"}))
    monkeypatch.delenv("CWA_CALIBRE_LIBRARY_DIR", raising=False)

    _auto_library(dirs_path, tmp_path / "mounted-library" / "nested").update_dirs_json()

    assert json.loads(dirs_path.read_text())["calibre_library_dir"] == str(
        tmp_path / "mounted-library" / "nested"
    )


def _command_stub(directory: Path, name: str, body: str) -> Path:
    stub = directory / name
    stub.write_text(f"#!/bin/sh\n{body}\n")
    stub.chmod(0o755)
    return stub


def _fake_root_path(tmp_path: Path, uid: str, *, install_body: str = "") -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _command_stub(bin_dir, "id", f"echo {uid}")
    if install_body:
        _command_stub(bin_dir, "install", install_body)
    return f"{bin_dir}:{os.environ['PATH']}"


def test_nonroot_image_setup_preserves_image_policy_file(tmp_path: Path):
    defaults = tmp_path / "defaults" / "policy.xml"
    defaults.parent.mkdir()
    defaults.write_text("default policy\n")
    policy = tmp_path / "ImageMagick" / "policy.xml"
    policy.parent.mkdir()
    policy.symlink_to(defaults)
    env = dict(os.environ, PATH=_fake_root_path(tmp_path, "1000"))

    result = subprocess.run(
        [str(IMAGE_POLICY_HELPER), str(policy), str(defaults)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert policy.is_symlink()
    assert policy.resolve() == defaults.resolve()
    assert policy.read_text() == "default policy\n"
    assert "not root" in result.stdout.lower()
    assert "Permission denied" not in result.stderr


def test_root_image_setup_keeps_policy_replacement(tmp_path: Path):
    policy = tmp_path / "ImageMagick" / "policy.xml"
    policy.parent.mkdir()
    policy.write_text("old\n")
    defaults = tmp_path / "defaults" / "policy.xml"
    defaults.parent.mkdir()
    defaults.write_text("image default\n")
    env = dict(os.environ, PATH=_fake_root_path(tmp_path, "0"))

    result = subprocess.run(
        [str(IMAGE_POLICY_HELPER), str(policy), str(defaults)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert policy.is_symlink()
    assert policy.resolve() == defaults.resolve()


def test_nonroot_runtime_directories_belong_to_the_running_uid(tmp_path: Path):
    paths = [
        tmp_path / "processed" / "imported",
        tmp_path / "metadata" / "logs",
        tmp_path / ".config" / "calibre-runtime",
        tmp_path / ".config" / "calibre" / "plugins",
    ]
    env = dict(os.environ, PATH=_fake_root_path(tmp_path, "1000"))

    result = subprocess.run(
        [str(RUNTIME_DIRS_HELPER), *(str(path) for path in paths)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for path in paths:
        assert path.is_dir()
        assert path.stat().st_uid == os.geteuid()
        probe = path / "writable-by-current-uid"
        probe.touch()
        probe.unlink()
    # Keep opt-in plugins separate from the config used by ingest/web services.
    assert not (tmp_path / ".config" / "calibre-runtime" / "plugins").exists()


def test_root_runtime_directories_keep_abc_ownership_request(tmp_path: Path):
    calls = tmp_path / "install-args.txt"
    install = (
        f'printf "%s\\n" "$*" >> {str(calls)!r}'
    )
    env = dict(os.environ, PATH=_fake_root_path(tmp_path, "0", install_body=install))
    paths = [tmp_path / "processed", tmp_path / "logs"]

    result = subprocess.run(
        [str(RUNTIME_DIRS_HELPER), *(str(path) for path in paths)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    invocations = calls.read_text().splitlines()
    assert invocations == [f"-d -o abc -g abc {path}" for path in paths]


def test_nonroot_runtime_directory_failure_is_not_hidden_by_a_later_success(
    tmp_path: Path,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _command_stub(bin_dir, "id", "echo 1000")
    _command_stub(
        bin_dir,
        "mkdir",
        'case "$*" in *blocked*) exit 7 ;; esac\nexec /bin/mkdir "$@"',
    )
    blocked = tmp_path / "blocked" / "first"
    later = tmp_path / "writable" / "later"

    result = subprocess.run(
        [str(RUNTIME_DIRS_HELPER), str(blocked), str(later)],
        text=True,
        capture_output=True,
        env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}"),
        check=False,
    )

    assert result.returncode != 0
    assert not later.exists()
