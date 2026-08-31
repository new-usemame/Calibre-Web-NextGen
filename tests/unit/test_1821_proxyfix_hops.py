# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for independently trusted ProxyFix header chains."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_ENV_VARS = (
    "TRUSTED_PROXY_COUNT",
    "PROXYFIX_X_FOR",
    "PROXYFIX_X_PROTO",
    "PROXYFIX_X_HOST",
)


def _constructed_proxyfix_args(overrides=None):
    env = os.environ.copy()
    for name in PROXY_ENV_VARS:
        env.pop(name, None)
    env.update(overrides or {})
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; import cps; middleware = cps.app.wsgi_app; "
                "print(json.dumps({name: getattr(middleware, name) for name in "
                "('x_for', 'x_proto', 'x_host', 'x_prefix')}))"
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_proxyfix_defaults_are_identical_to_the_existing_single_hop_behavior():
    assert _constructed_proxyfix_args() == {
        "x_for": 1,
        "x_proto": 1,
        "x_host": 1,
        "x_prefix": 1,
    }


def test_per_header_env_vars_change_the_proxyfix_construction_args():
    assert _constructed_proxyfix_args({
        "PROXYFIX_X_FOR": "2",
        "PROXYFIX_X_PROTO": "3",
        "PROXYFIX_X_HOST": "4",
    }) == {
        "x_for": 2,
        "x_proto": 3,
        "x_host": 4,
        "x_prefix": 1,
    }


def test_per_header_values_override_the_backward_compatible_shared_count():
    assert _constructed_proxyfix_args({
        "TRUSTED_PROXY_COUNT": "2",
        "PROXYFIX_X_PROTO": "3",
    }) == {
        "x_for": 2,
        "x_proto": 3,
        "x_host": 2,
        "x_prefix": 2,
    }
