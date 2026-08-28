# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural proofs for #1927's E2E trigger and image-subject gate."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.ci_path_classification import classify_paths, concurrency_paths


pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
TESTS_WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"
DEV_WORKFLOW = REPO / ".github" / "workflows" / "docker-image-build-dev.yml"
RESOLVER = REPO / "scripts" / "resolve-e2e-image.sh"


@pytest.mark.parametrize(
    "path",
    [
        "cps/ub.py",
        "cps/db.py",
        "cps/__init__.py",
        "cps/server.py",
        "cps/gevent_wsgi.py",
        "cps/services/annotation_sync/hardcover.py",
        "cps/annotations.py",
    ],
)
def test_named_engine_and_concurrency_surfaces_trigger_e2e(path):
    result = classify_paths([path], REPO)
    assert result["concurrency"] is True, path


def test_unrelated_backend_route_does_not_pay_for_concurrency_e2e():
    result = classify_paths(["cps/api/books.py"], REPO)
    assert result["build"] is True
    assert result["concurrency"] is False


def test_concurrency_set_derives_new_helpers_from_imports(tmp_path):
    cps = tmp_path / "cps"
    cps.mkdir()
    (cps / "__init__.py").write_text("", encoding="utf-8")
    (cps / "ub.py").write_text("from . import engine_helper\n", encoding="utf-8")
    (cps / "engine_helper.py").write_text("from . import sqlite_tuning\n", encoding="utf-8")
    (cps / "sqlite_tuning.py").write_text("WAL = True\n", encoding="utf-8")
    (cps / "unrelated.py").write_text("VALUE = 1\n", encoding="utf-8")

    derived = concurrency_paths(tmp_path)
    assert "cps/engine_helper.py" in derived
    assert "cps/sqlite_tuning.py" in derived
    assert "cps/unrelated.py" not in derived


def test_workflows_wire_nonfork_concurrency_to_a_commit_image_and_hard_gate():
    tests = TESTS_WORKFLOW.read_text(encoding="utf-8")
    dev = DEV_WORKFLOW.read_text(encoding="utf-8")

    assert "outputs.concurrency == 'true'" in tests
    assert "github.event.pull_request.head.repo.fork != true" in tests
    assert "IS_CONCURRENCY_PR" in tests
    assert 'needs.e2e-tests.result }}" != "success"' in tests
    assert "sha-$SUBJECT_SHA" in tests
    assert "resolve-e2e-image.sh" in tests
    assert "pull_request:" in dev
    assert "sha-$(git rev-parse HEAD)" in dev
    assert "full_build=$full_build" in dev


def _stubbed_resolve(tmp_path: Path, succeeds_on: int | None, digest: str | None = None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "calls"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "n=0; [[ -f \"$STUB_STATE\" ]] && n=$(cat \"$STUB_STATE\")\n"
        "n=$((n+1)); echo \"$n\" > \"$STUB_STATE\"\n"
        "if [[ -n \"${STUB_SUCCEEDS_ON:-}\" && $n -ge $STUB_SUCCEEDS_ON ]]; then\n"
        "  printf '\"%s\"\\n' \"${STUB_DIGEST}\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_STATE": str(state),
        "STUB_SUCCEEDS_ON": "" if succeeds_on is None else str(succeeds_on),
        "STUB_DIGEST": digest or "sha256:" + "a" * 64,
    }
    proc = subprocess.run(
        ["bash", str(RESOLVER), "registry.example/project/app", "sha-deadbeef", "3", "0"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc, int(state.read_text(encoding="utf-8"))


def test_resolver_waits_then_returns_only_an_immutable_digest(tmp_path):
    proc, calls = _stubbed_resolve(tmp_path, succeeds_on=3)
    assert proc.returncode == 0, proc.stderr
    assert calls == 3
    assert proc.stdout.strip() == "registry.example/project/app@sha256:" + "a" * 64
    assert "attempt 3/3" in proc.stderr


def test_resolver_fails_loudly_without_falling_back_to_dev(tmp_path):
    proc, calls = _stubbed_resolve(tmp_path, succeeds_on=None)
    assert proc.returncode == 1
    assert calls == 3
    assert proc.stdout == ""
    assert "Refusing to run E2E" in proc.stderr
    assert ":dev" not in proc.stderr


def test_resolver_rejects_a_non_digest_response(tmp_path):
    proc, calls = _stubbed_resolve(tmp_path, succeeds_on=1, digest="dev")
    assert proc.returncode == 1
    assert calls == 3
    assert proc.stdout == ""
