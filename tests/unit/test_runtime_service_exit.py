# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later


"""The standalone supervisor exit record remains durable and validates inputs."""

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_service_exit_recorder_replaces_only_its_owned_record(tmp_path):
    """A service exit is retained as numeric supervisor evidence, atomically.

    The recorder does not accept an arbitrary destination through its CLI; this
    direct function probe supplies a temporary owned directory for the same write.
    Dropping numeric validation or atomic replacement makes this fail.
    """
    script = Path(__file__).parents[2] / "scripts" / "record_runtime_service_exit.py"
    spec = importlib.util.spec_from_file_location("runtime_exit_recorder", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = module.record_exit(str(tmp_path), "-1", "9", now=100)
    second = module.record_exit(str(tmp_path), "0", "0", now=101)
    path = tmp_path / module.RECORD_NAME

    assert first["exit_code"] == -1
    assert json.loads(path.read_text()) == second
    with pytest.raises(ValueError):
        module.record_exit(str(tmp_path), "not-a-code", "0", now=102)
    assert json.loads(path.read_text()) == second
