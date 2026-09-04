# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation harness must never turn a non-result into a verdict.

Every failure mode this guards was observed by hand in one session: a stale
anchor leaving the mutant unapplied while pytest printed green, two modules
with the same basename overwriting each other's backup, and a restore that was
assumed rather than checked.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess

import pytest

pytestmark = pytest.mark.unit

_HARNESS = pathlib.Path(__file__).resolve().parents[1] / "mutation" / "mutate.py"
_spec = importlib.util.spec_from_file_location("mutate", _HARNESS)
mutate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _committed_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Mutation Tests")
    _git(repo, "config", "user.email", "mutation-tests.invalid")
    (repo / ".gitignore").write_text("*.ignored\n__pycache__/\n")
    (repo / "victim.py").write_text("VALUE = 1\n")
    (repo / "collateral.py").write_text("ORIGINAL\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "seed")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_isolated_sweep_scrubs_the_full_tree_to_an_immutable_seed(tmp_path):
    repo, seed = _committed_repo(tmp_path)
    state = tmp_path / "state"
    live_before = (repo / "victim.py").read_bytes()

    with mutate.IsolatedSweep.create(repo, seed, state_root=state) as sweep:
        assert sweep.root != repo
        assert _git(sweep.root, "rev-parse", "HEAD") == seed
        (sweep.root / "victim.py").write_text("VALUE = 2\n")
        (sweep.root / "collateral.py").write_text("DAMAGED\n")
        (sweep.root / "run.ignored").write_text("artifact\n")
        (sweep.root / "__pycache__").mkdir()
        (sweep.root / "__pycache__" / "victim.pyc").write_bytes(b"bytecode")
        _git(sweep.root, "add", "victim.py")
        _git(sweep.root, "commit", "-qm", "phase changed HEAD")

        witness = sweep.scrub()

        assert witness == {
            "seed_sha": seed,
            "seed_tree": _git(repo, "rev-parse", f"{seed}^{{tree}}"),
            "head": seed,
            "status_empty": True,
        }
        assert (sweep.root / "victim.py").read_text() == "VALUE = 1\n"
        assert (sweep.root / "collateral.py").read_text() == "ORIGINAL\n"
        assert not (sweep.root / "run.ignored").exists()
        assert not (sweep.root / "__pycache__").exists()
        disposable_root = sweep.root

    assert not disposable_root.exists()
    assert (repo / "victim.py").read_bytes() == live_before
    assert _git(repo, "status", "--porcelain") == ""


def test_startup_reaps_a_dead_tool_owned_sweep(tmp_path):
    repo, seed = _committed_repo(tmp_path)
    state = tmp_path / "state"
    abandoned = mutate.IsolatedSweep.create(repo, seed, state_root=state)
    metadata_path = abandoned.entry / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["owner_pid"] = 2_000_000_000
    metadata_path.write_text(json.dumps(metadata))
    abandoned._closed = True

    replacement = mutate.IsolatedSweep.create(repo, seed, state_root=state)
    try:
        assert not abandoned.root.exists()
        assert replacement.root.exists()
    finally:
        replacement.close()


def test_startup_does_not_reap_a_live_sweep(tmp_path):
    repo, seed = _committed_repo(tmp_path)
    state = tmp_path / "state"
    first = mutate.IsolatedSweep.create(repo, seed, state_root=state)
    second = mutate.IsolatedSweep.create(repo, seed, state_root=state)
    try:
        assert first.root.exists()
        assert second.root.exists()
        metadata = json.loads((first.entry / "metadata.json").read_text())
        assert metadata["owner_pid"] == os.getpid()
    finally:
        second.close()
        first.close()


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
