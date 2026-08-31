# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation harness must never turn a non-result into a verdict.

Every failure mode this guards was observed by hand in one session: a stale
anchor leaving the mutant unapplied while pytest printed green, two modules
with the same basename overwriting each other's backup, and a restore that was
assumed rather than checked.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

pytestmark = pytest.mark.unit

_HARNESS = pathlib.Path(__file__).resolve().parents[1] / "mutation" / "mutate.py"
_spec = importlib.util.spec_from_file_location("mutate", _HARNESS)
mutate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)


def test_backup_names_are_path_derived_not_basename():
    """cps/admin.py and cps/api/admin.py must not share a backup file."""
    a = mutate._backup_name("cps/admin.py")
    b = mutate._backup_name("cps/api/admin.py")
    assert a != b, (
        "same-basename modules collide, so restoring one writes the other's "
        "contents over it — this clobbered a 3,788-line module once already"
    )


def test_a_stale_anchor_is_an_error_never_a_verdict(tmp_path, monkeypatch):
    victim = tmp_path / "cps" / "thing.py"
    victim.parent.mkdir(parents=True)
    victim.write_text("value = 1\n")
    monkeypatch.setattr(mutate, "REPO", tmp_path)

    result = mutate.run_mutant("stale", "cps/thing.py", "NOT PRESENT", "x", ["ignored"])

    assert result["status"] == "ERROR", (
        "an unapplied mutant must never be reported as caught or survived — "
        "a green pytest summary is indistinguishable from a missed defect"
    )
    assert "NOT applied" in result["detail"]
    assert victim.read_text() == "value = 1\n", "the file was touched despite the error"


def test_an_ambiguous_anchor_is_also_an_error(tmp_path, monkeypatch):
    victim = tmp_path / "cps" / "thing.py"
    victim.parent.mkdir(parents=True)
    victim.write_text("x = 1\nx = 1\n")
    monkeypatch.setattr(mutate, "REPO", tmp_path)

    result = mutate.run_mutant("ambiguous", "cps/thing.py", "x = 1", "x = 2", ["ignored"])

    assert result["status"] == "ERROR" and "matched 2 times" in result["detail"], (
        "replacing the first of several identical anchors mutates a line the "
        "author did not choose"
    )


def test_a_surviving_mutant_is_reported_and_the_file_is_restored(tmp_path, monkeypatch):
    victim = tmp_path / "cps" / "thing.py"
    victim.parent.mkdir(parents=True)
    original = "GUARD = True\n"
    victim.write_text(original)
    monkeypatch.setattr(mutate, "REPO", tmp_path)
    # A suite that passes no matter what the code says: the mutant survives.
    monkeypatch.setattr(mutate.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "1 passed"})())

    result = mutate.run_mutant("survivor", "cps/thing.py", "GUARD = True",
                               "GUARD = False", ["ignored"])

    assert result["status"] == "SURVIVED", "a mutant the suite ignores must be reported, loudly"
    assert victim.read_text() == original, "the harness left the mutation in the working tree"
