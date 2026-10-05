# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone conversion owns maintenance before accessing its library."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_cli_owns_maintenance_before_library_access_and_releases_on_failure(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    converter = importlib.import_module("convert_library")
    monkeypatch.setattr(converter.app_paths, "config_dir", lambda: tmp_path)
    observed = []

    def library_access():
        assert converter.ownership.busy(str(tmp_path), "maintenance")
        kwargs = converter._child_ownership()
        if os.name != "nt":
            fd = kwargs["pass_fds"][0]
            child = subprocess.run([sys.executable, "-c", "import os,sys;os.fstat(int(sys.argv[1]))", str(fd)],
                                   **kwargs, capture_output=True)
            assert child.returncode == 0, child.stderr.decode()
        observed.append(True)
        raise RuntimeError("conversion failed")

    monkeypatch.setattr(converter, "_main", library_access)
    with pytest.raises(RuntimeError, match="conversion failed"):
        converter.main()
    assert observed == [True]
    assert not converter.ownership.busy(str(tmp_path), "maintenance")
    assert converter._child_ownership() == {}
