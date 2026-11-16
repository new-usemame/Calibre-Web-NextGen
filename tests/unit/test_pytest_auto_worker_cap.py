# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""`-n auto` must not start one worker per core on a shared workstation.

The tests call the hook the way xdist does, through the plugin manager, so they
also fail if the conftest implementation stops being registered.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit

xdist_plugin = pytest.importorskip("xdist.plugin")


def _auto_workers(config):
    return config.hook.pytest_xdist_auto_num_workers(config=config)


@pytest.mark.parametrize("ci", [None, "0", "false"])
def test_local_auto_is_capped_at_four_on_a_many_core_machine(request, monkeypatch, ci):
    if ci is None:
        monkeypatch.delenv("CI", raising=False)
    else:
        monkeypatch.setenv("CI", ci)
    monkeypatch.delenv("CWNG_PYTEST_AUTO_WORKERS", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 16)

    assert _auto_workers(request.config) == 4


def test_local_auto_never_exceeds_the_cores_present(request, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("CWNG_PYTEST_AUTO_WORKERS", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 2)

    assert _auto_workers(request.config) == 2


def test_explicit_local_override_wins(request, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("CWNG_PYTEST_AUTO_WORKERS", "7")

    assert _auto_workers(request.config) == 7


def test_ci_keeps_xdists_own_answer(request, monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("CWNG_PYTEST_AUTO_WORKERS", raising=False)

    assert _auto_workers(request.config) == xdist_plugin.pytest_xdist_auto_num_workers(request.config)
