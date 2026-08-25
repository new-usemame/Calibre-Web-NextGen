# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The unit suite writes real annotation backups into the checkout (#1712).

``annotation_backup.get_backup_root()`` resolves ``CONFIG_DIR/annotation-backups``
at call time. In the container CONFIG_DIR is ``/config``; under pytest it is the
repository, so running the suite leaves real gzipped highlight payloads at
``<repo>/annotation-backups/<user_id>/<book_id>/<UTC>.json.gz``. They are user
data, so the hazard is that an ``git add -A`` after a local run commits them.

Ignoring the directory does not stop the writes -- it stops them being
committable. The directory name is read back from the service rather than
hardcoded, so renaming it in code without updating .gitignore fails here.
"""
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_annotation_backup_directory_is_git_ignored():
    from cps.services import annotation_backup

    # The name the service actually writes, not a copy of it.
    directory = annotation_backup.get_backup_root().name
    assert directory, "get_backup_root() must resolve to a named directory"

    patterns = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    accepted = {
        "/%s/" % directory, "/%s" % directory,
        "%s/" % directory, directory,
    }
    assert patterns & accepted, (
        "%r is written into the repository by the test suite and must be "
        "git-ignored; .gitignore has none of %r" % (directory, sorted(accepted))
    )


@pytest.mark.unit
def test_backup_root_follows_config_dir(monkeypatch, tmp_path):
    """Redirecting CONFIG_DIR moves the backups -- the basis for a real fix."""
    from cps import constants
    from cps.services import annotation_backup

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    assert annotation_backup.get_backup_root() == tmp_path / "annotation-backups"
