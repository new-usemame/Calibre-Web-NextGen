# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pin the build-time Calibre /usr/bin symlink stanza (#875).

Calibre's binaries have always shipped inside the image, but the
``/usr/bin`` entry points did not. So ``calibredb --version`` failed on a
cold boot, the ``calibre-binaries-setup`` s6 service concluded Calibre was
"not installed", and it ran ``calibre_postinstall`` on every single start --
about 12s of a ~60s startup, on every restart, forever.

The Dockerfile now creates those links at build time. These tests do two
things:

1. Extract the shipped ``find ... -exec ln`` expression out of the
   Dockerfile and *execute it* against a synthetic ``/app/calibre`` tree, so
   the assertions are about what the command actually does, not about which
   words appear in the file. A rewrite that keeps the words but breaks the
   behaviour goes red.
2. Pin the two invariants that are easy to lose in a later edit: the
   verification step that fails the build if ``calibredb`` never got linked,
   and the fact that the runtime service is kept as a fallback rather than
   deleted.

The link set deliberately mirrors what ``calibre_postinstall`` itself
creates -- every top-level executable except the installer and
``calibre-complete`` (upstream reaches the completion helper through the
completion scripts, not through PATH). Linking extra files would leave
``/usr/bin/calibre_postinstall`` and a stray marker file on PATH.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
SETUP_SERVICE = (
    REPO_ROOT / "root" / "etc" / "s6-overlay" / "s6-rc.d" / "calibre-binaries-setup" / "run"
)

# The executables calibre ships at the top level of its linux bundle, as
# observed in the shipped image (calibre 9.1). `calibre_postinstall` and
# `calibre-complete` are present too but are not user entry points.
CALIBRE_ENTRY_POINTS = [
    "calibre",
    "calibre-customize",
    "calibredb",
    "calibre-debug",
    "calibre-parallel",
    "calibre-server",
    "calibre-smtp",
    "ebook-convert",
    "ebook-device",
    "ebook-edit",
    "ebook-meta",
    "ebook-polish",
    "ebook-viewer",
    "fetch-ebook-metadata",
    "lrf2lrs",
    "lrfviewer",
    "lrs2lrf",
    "markdown-calibre",
    "web2disk",
]
NOT_ENTRY_POINTS = ["calibre_postinstall", "calibre-complete"]


@pytest.fixture(scope="module")
def link_command() -> str:
    """Pull the shipped `find ... -exec ln ...` command out of the Dockerfile.

    Returns the shell body of the RUN instruction with line continuations
    joined, so it can be handed to /bin/sh verbatim.
    """
    text = DOCKERFILE.read_text()
    match = re.search(
        r"^RUN (find /app/calibre(?:[^\n]*\\\n)*[^\n]*)$",
        text,
        re.MULTILINE,
    )
    assert match, (
        "Dockerfile must contain a `RUN find /app/calibre ...` stanza that "
        "creates the /usr/bin entry points at build time (#875)."
    )
    return match.group(1).replace("\\\n", " ")


def _run_shipped_command(command: str, tmp_path: Path) -> Path:
    """Execute the shipped command against a synthetic tree.

    Builds a fake /app/calibre containing every top-level file the real
    bundle has, plus a fake /usr/bin, then rewrites only the two absolute
    paths so the command under test is otherwise byte-identical to what the
    image build runs.
    """
    calibre_dir = tmp_path / "app" / "calibre"
    usr_bin = tmp_path / "usr" / "bin"
    (calibre_dir / "lib").mkdir(parents=True)
    usr_bin.mkdir(parents=True)

    for name in CALIBRE_ENTRY_POINTS + NOT_ENTRY_POINTS:
        target = calibre_dir / name
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
    # Non-executable top-level files the real tree also carries. The Qt6
    # sentinel is written at runtime by cwa-init, but a later edit that
    # drops the -perm filter would sweep files of this shape onto PATH.
    (calibre_dir / ".qt6_processed").write_text("")
    (calibre_dir / "README.txt").write_text("not an entry point")
    # A nested executable, to prove -maxdepth 1 still holds.
    nested = calibre_dir / "lib" / "nested-binary"
    nested.write_text("#!/bin/sh\nexit 0\n")
    nested.chmod(0o755)

    localized = command.replace("/app/calibre", str(calibre_dir)).replace(
        "/usr/bin", str(usr_bin)
    )
    result = subprocess.run(
        ["/bin/sh", "-c", localized],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, (
        f"shipped link command failed: rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    return usr_bin


@pytest.mark.skipif(
    shutil.which("find") is None, reason="find(1) not available on this host"
)
def test_every_calibre_entry_point_is_linked(link_command: str, tmp_path: Path) -> None:
    """The behavioural assertion #875 is about: calibredb and friends resolve
    from PATH in the built image, so the boot-time check passes."""
    usr_bin = _run_shipped_command(link_command, tmp_path)
    missing = [n for n in CALIBRE_ENTRY_POINTS if not (usr_bin / n).is_symlink()]
    assert not missing, (
        f"these Calibre entry points were not linked into /usr/bin: {missing}. "
        "calibre-binaries-setup checks `calibredb --version`; if calibredb is "
        "missing the service runs calibre_postinstall on every start again."
    )
    resolved = os.readlink(usr_bin / "calibredb")
    assert resolved.endswith("/calibredb"), (
        f"/usr/bin/calibredb should point at the bundled binary, got {resolved}"
    )


@pytest.mark.skipif(
    shutil.which("find") is None, reason="find(1) not available on this host"
)
def test_non_entry_points_are_not_put_on_path(
    link_command: str, tmp_path: Path
) -> None:
    """Naive `-type f` over the bundle also drags the installer, the
    completion helper and non-executable marker files onto PATH."""
    usr_bin = _run_shipped_command(link_command, tmp_path)
    strays = sorted(
        p.name
        for p in usr_bin.iterdir()
        if p.name not in CALIBRE_ENTRY_POINTS
    )
    assert not strays, (
        f"/usr/bin picked up files that are not Calibre entry points: {strays}. "
        "calibre_postinstall does not put these on PATH and neither should the "
        "build."
    )


@pytest.mark.skipif(
    shutil.which("find") is None, reason="find(1) not available on this host"
)
def test_linking_is_idempotent(link_command: str, tmp_path: Path) -> None:
    """`ln` without -f aborts the whole RUN when a link already exists, which
    would break any future stage that runs this twice or ships a base image
    that already carries one of the names."""
    usr_bin = _run_shipped_command(link_command, tmp_path)
    first = sorted(p.name for p in usr_bin.iterdir())

    calibre_dir = tmp_path / "app" / "calibre"
    localized = link_command.replace("/app/calibre", str(calibre_dir)).replace(
        "/usr/bin", str(usr_bin)
    )
    second_run = subprocess.run(
        ["/bin/sh", "-c", localized], capture_output=True, text=True, cwd=tmp_path
    )
    assert second_run.returncode == 0, (
        "re-running the link stanza must succeed (use `ln -sf`), got "
        f"rc={second_run.returncode} stderr={second_run.stderr}"
    )
    assert sorted(p.name for p in usr_bin.iterdir()) == first


def test_build_fails_loudly_when_calibredb_is_not_linked(link_command: str) -> None:
    """A silent no-op here would reintroduce the 12s-per-boot cost without any
    signal, so the build verifies the result. `test -x` resolves the symlink
    without executing the binary, which matters under cross-arch buildx."""
    assert "test -x /usr/bin/calibredb" in link_command, (
        "the build-time link stanza must verify /usr/bin/calibredb exists and "
        "is executable, so a layout change in a future Calibre release fails "
        "the build instead of silently restoring the per-boot postinstall."
    )
    assert "calibredb --version" not in link_command, (
        "verify with `test -x`, not by running the binary: the arm64 image is "
        "built under emulation where executing the target is slow or fails."
    )


def test_runtime_service_is_retained_as_fallback() -> None:
    """The s6 service is now a no-op on our own image, but it is the only
    thing that repairs an image whose links are missing -- deleting it would
    turn a recoverable state into a broken container."""
    assert SETUP_SERVICE.exists(), (
        "calibre-binaries-setup must stay in place as the fallback path even "
        "though the symlinks are now baked at build time (#875)."
    )
    body = SETUP_SERVICE.read_text()
    assert "calibre_postinstall" in body
    assert "Skipping setup, Calibre already installed" in body, (
        "the service must keep its already-installed branch -- that is the "
        "branch the baked symlinks are designed to hit."
    )
