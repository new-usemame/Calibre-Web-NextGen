# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A change to the integration fixtures must run the lane those fixtures gate.

``tests/conftest.py`` owns ``cwa_api_client``: it decides whether the Docker
integration tests can reach and authenticate against the container. A change
there can disable the entire lane without touching a single test file.

That is not hypothetical. The fixture signed in without a CSRF token, the login
began rejecting it, and the fixture reported the rejection as a skip -- 48 tests
stopped running and CI kept reporting the lane green. Nothing in the path
classifier would have run the lane on the commit that broke it, because
``tests/conftest.py`` is not ``tests/integration/``.

Changing one integration test already runs the lane. Changing the fixture every
one of them depends on must too.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci_path_classification import classify_paths

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build(*paths: str) -> bool:
    """``build`` is what gates Integration Tests (Docker) on a pull request."""
    return classify_paths(list(paths), REPO_ROOT)["build"]


def test_changing_the_shared_test_fixtures_runs_the_integration_lane():
    assert _build("tests/conftest.py"), (
        "a change to tests/conftest.py can silently disable the Docker "
        "integration lane, so it must trigger that lane"
    )


def test_changing_an_integration_test_still_runs_the_lane():
    """The established behaviour this extends, pinned so it cannot regress."""
    assert _build("tests/integration/test_ingest_pipeline.py")


def test_an_unrelated_test_file_does_not_pay_the_integration_cost():
    """The exemption is the point of the classifier; widening it to all of
    tests/ would make every unit-test PR wait fifteen minutes."""
    assert not _build("tests/unit/test_some_pure_unit_behaviour.py")
