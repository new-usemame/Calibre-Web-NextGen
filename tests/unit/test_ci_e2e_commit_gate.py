# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural proofs for #1927's E2E trigger and image-subject gate."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from scripts.ci_path_classification import _path_to_module, classify_paths, concurrency_paths


pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
TESTS_WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"
DEV_WORKFLOW = REPO / ".github" / "workflows" / "docker-image-build-dev.yml"
RESOLVER = REPO / "scripts" / "resolve-e2e-image.sh"
ALIASER = REPO / "scripts" / "alias-e2e-image.sh"


@pytest.mark.parametrize(
    "path",
    [
        "cps/ub.py",
        "cps/db.py",
        "cps/__init__.py",
        "cps/kobo.py",
        "cps/server.py",
        "cps/gevent_wsgi.py",
        "cps/services/annotation_sync/hardcover.py",
        "cps/annotations.py",
        "cps/web.py",
        "cps/api/shelves.py",
    ],
)
def test_named_engine_and_concurrency_surfaces_trigger_e2e(path):
    result = classify_paths([path], REPO)
    assert result["concurrency"] is True, path


def test_unrelated_backend_module_does_not_pay_for_concurrency_e2e():
    result = classify_paths(["cps/audio.py"], REPO)
    assert result["build"] is True
    assert result["concurrency"] is False


@pytest.mark.parametrize("path", [
    "cps/spa.py",
    "cps/web.py",
    "cps/templates/layout.html",
])
def test_ui_routing_surfaces_trigger_frontend_e2e(path):
    """A backend-only landing decision must still run the browser contract."""
    result = classify_paths([path], REPO)
    assert result["frontend"] is True, path


def test_unrelated_classic_template_does_not_trigger_frontend_e2e():
    result = classify_paths(["cps/templates/book_edit.html"], REPO)
    assert result["frontend"] is False


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


def test_documented_classifier_scope_matches_the_tree_and_names_the_real_limit():
    derived = concurrency_paths(REPO)
    modules = {
        module
        for path in (REPO / "cps").rglob("*.py")
        if (module := _path_to_module(path.relative_to(REPO).as_posix())) is not None
    }
    readme = (REPO / "frontend" / "e2e" / "README.md").read_text(encoding="utf-8")
    assert f"{len(derived)} of {len(modules)} local Python modules" in readme
    assert "reverse dependents cannot be discovered" in readme
    assert "two-level cutoff" in readme


def test_concurrency_closure_is_independent_of_python_hash_order():
    command = (
        "import json; from pathlib import Path; "
        "from scripts.ci_path_classification import concurrency_paths; "
        "print(json.dumps(sorted(concurrency_paths(Path.cwd()))))"
    )
    path_sets = set()
    for seed in ("1", "2", "3", "42"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            ["python3", "-c", command],
            cwd=REPO,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        path_sets.add(frozenset(json.loads(proc.stdout)))
    assert path_sets == {frozenset(concurrency_paths(REPO))}


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


def test_pr_classification_executes_the_base_copy_in_both_consumers():
    """A PR must not narrow the policy that decides whether its gate runs."""
    for workflow in (TESTS_WORKFLOW, DEV_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        assert 'classifier_ref="$BASE_SHA"' in text, workflow.name
        assert 'classifier_ref="$HEAD_SHA"' not in text
        assert "frontend=true\\nbuild=true\\nconcurrency=true\\nimage=true" in text
        assert 'git cat-file -e "$classifier_ref:.dockerignore"' in text
        assert (
            'git archive "$classifier_ref" cps .dockerignore '
            'scripts/ci_path_classification.py' in text
        )
        assert 'python3 "$classifier_root/scripts/ci_path_classification.py"' in text
        assert "| python3 scripts/ci_path_classification.py --github-output" not in text


def test_wait_budget_covers_the_build_pipeline_and_retrying_suite():
    """The paired resolver is bounded by the producer's own job budgets."""
    import re
    import yaml

    tests = yaml.safe_load(TESTS_WORKFLOW.read_text(encoding="utf-8"))
    dev = yaml.safe_load(DEV_WORKFLOW.read_text(encoding="utf-8"))
    e2e = tests["jobs"]["e2e-tests"]
    resolve = next(step for step in e2e["steps"] if step.get("name") == "Resolve image to test")
    script = resolve["run"]
    paired_attempt = re.search(
        r"paired with.*?attempts=(\d+)", script, flags=re.DOTALL
    )
    assert paired_attempt
    wait_seconds = (int(paired_attempt.group(1)) - 1) * 30

    jobs = dev["jobs"]
    producer_minutes = (
        jobs["classify"]["timeout-minutes"]
        + jobs["ensure-mirror"]["timeout-minutes"]
        + max(jobs["build-amd64"]["timeout-minutes"], jobs["build-arm"]["timeout-minutes"])
        + jobs["merge"]["timeout-minutes"]
    )
    assert wait_seconds == producer_minutes * 60
    assert e2e["timeout-minutes"] * 60 - wait_seconds >= 60 * 60

    alias = jobs["alias-image-neutral-commit"]
    publish = next(
        step for step in alias["steps"] if step.get("name") == "Publish immutable commit alias"
    )
    assert alias["timeout-minutes"] >= producer_minutes + 5
    assert "191 30" in publish["run"]
    assert (publish.get("env") or {}).get("GITHUB_TOKEN")
    assert (e2e.get("permissions") or {}).get("actions") == "read"


def test_dev_push_fails_fast_instead_of_waiting_for_a_nonexistent_producer():
    import yaml

    workflow = yaml.safe_load(TESTS_WORKFLOW.read_text(encoding="utf-8"))
    e2e = workflow["jobs"]["e2e-tests"]
    refusal = next(
        step for step in e2e["steps"] if step.get("name") == "Refuse unsupported dev-branch image wait"
    )
    condition = str(refusal.get("if") or "")
    assert "refs/heads/dev" in condition and "github.event_name == 'push'" in condition
    assert "exit 1" in refusal["run"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str, path: str, content: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return _git(repo, "rev-parse", "HEAD")


def _stage_alias_test_dockerignore(repo: Path) -> None:
    """Give history fixtures the same SSOT the production classifier requires."""
    (repo / ".dockerignore").write_text("docs\n", encoding="utf-8")
    _git(repo, "add", ".dockerignore")


class _ActionsApi:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = responses
        self.run_requests = 0
        self.job_requests = 0
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib handler API
                if "/actions/workflows/docker-image-build-dev.yml/runs" in self.path:
                    index = min(api.run_requests, len(api.responses) - 1)
                    response = dict(api.responses[index])
                    api.run_requests += 1
                    if response.pop("absent", False):
                        payload = {"workflow_runs": []}
                        self._send_json(payload)
                        return
                    response.setdefault("id", 77)
                    response.setdefault("html_url", "https://github.example/runs/77")
                    response["jobs_url"] = f"{api.url}/repos/example/project/actions/runs/77/jobs"
                    payload = {"workflow_runs": [response]}
                elif self.path.startswith("/repos/example/project/actions/runs/77/jobs"):
                    api.job_requests += 1
                    index = min(max(api.run_requests - 1, 0), len(api.responses) - 1)
                    payload = {"jobs": api.responses[index].get("jobs", [])}
                else:
                    self.send_error(404)
                    return
                self._send_json(payload)

            def _send_json(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _run_alias(
    tmp_path: Path,
    source: str,
    subject: str,
    *,
    dev_matches: bool = True,
    source_succeeds_on: int = 1,
    api_url: str | None = None,
    recovery_output: Path | None = None,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    log.unlink(missing_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "if [[ \"$1 $2 $3\" == \"buildx imagetools inspect\" ]]; then\n"
        "  if [[ \"$4\" == *:dev ]]; then printf '\"%s\"\\n' \"$DEV_DIGEST\"; exit 0; fi\n"
        "  n=0; [[ -f \"$SOURCE_STATE\" ]] && n=$(cat \"$SOURCE_STATE\")\n"
        "  n=$((n+1)); echo \"$n\" > \"$SOURCE_STATE\"\n"
        "  if (( n < SOURCE_SUCCEEDS_ON )); then exit 1; fi\n"
        "  printf '\"%s\"\\n' \"$SOURCE_DIGEST\"\n"
        "  exit 0\n"
        "fi\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    digest = "sha256:" + "a" * 64
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_WORKSPACE": str(tmp_path),
        "CLASSIFIER_PATH": str(REPO / "scripts" / "ci_path_classification.py"),
        "SOURCE_DIGEST": digest,
        "DEV_DIGEST": digest if dev_matches else "sha256:" + "b" * 64,
        "DOCKER_LOG": str(log),
        "SOURCE_STATE": str(tmp_path / "source-calls"),
        "SOURCE_SUCCEEDS_ON": str(source_succeeds_on),
        "GITHUB_TOKEN": "test-token",
    }
    command = ["bash", str(ALIASER), "registry.example/project/app", source, subject]
    if api_url:
        command.extend(["4", "0", "example/project", "push", api_url])
        if recovery_output is not None:
            command.append(str(recovery_output))
    proc = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc, log.read_text(encoding="utf-8") if log.exists() else ""


def test_neutral_alias_requires_the_newest_relevant_ancestor_and_matching_dev(tmp_path):
    _git(tmp_path, "init", "-q")
    _stage_alias_test_dockerignore(tmp_path)
    source = _commit(tmp_path, "backend", "cps/web.py", "WRITE = 1\n")
    subject = _commit(tmp_path, "docs", "docs/usage.md", "docs\n")

    proc, log = _run_alias(tmp_path, source, subject)
    assert proc.returncode == 0, proc.stderr
    assert f"-t registry.example/project/app:sha-{subject}" in log
    assert "registry.example/project/app@sha256:" + "a" * 64 in log

    stale, log = _run_alias(tmp_path, source, subject, dev_matches=False)
    assert stale.returncode == 1
    assert ":dev does not name source commit" in stale.stderr
    assert log == ""


def test_neutral_alias_rejects_an_intervening_image_relevant_commit(tmp_path):
    _git(tmp_path, "init", "-q")
    _stage_alias_test_dockerignore(tmp_path)
    old_source = _commit(tmp_path, "old backend", "cps/web.py", "WRITE = 1\n")
    _commit(tmp_path, "middle docs", "docs/one.md", "one\n")
    new_source = _commit(tmp_path, "new backend", "cps/kobo.py", "WRITE = 2\n")
    subject = _commit(tmp_path, "new docs", "docs/two.md", "two\n")

    proc, log = _run_alias(tmp_path, old_source, subject)
    assert proc.returncode == 1
    assert "is not the newest image-relevant ancestor" in proc.stderr
    assert f"Expected source: {new_source}" in proc.stderr
    assert log == ""


def _stubbed_resolve(
    tmp_path: Path,
    succeeds_on: int | None,
    digest: str | None = None,
    *,
    api_url: str | None = None,
):
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
        "GITHUB_TOKEN": "test-token",
    }
    command = [
        "bash",
        str(RESOLVER),
        "registry.example/project/app",
        "sha-deadbeef",
        "3",
        "0",
    ]
    if api_url:
        command.extend(["example/project", "a" * 40, "push", api_url])
    proc = subprocess.run(
        command,
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


def test_resolver_stops_when_exact_sha_producer_has_failed(tmp_path):
    responses = [{"status": "completed", "conclusion": "failure"}]
    with _ActionsApi(responses) as api:
        proc, calls = _stubbed_resolve(tmp_path, succeeds_on=None, api_url=api.url)

    assert proc.returncode == 1
    assert calls == 1
    assert api.run_requests == 1
    assert "https://github.example/runs/77" in proc.stderr
    assert "concluded failure" in proc.stderr


def test_resolver_fails_immediately_when_no_exact_producer_exists(tmp_path):
    with _ActionsApi([{"absent": True}]) as api:
        proc, calls = _stubbed_resolve(tmp_path, succeeds_on=None, api_url=api.url)

    assert proc.returncode == 1
    assert calls == 1
    assert api.run_requests == 1
    assert "no Build & Push producer run exists" in proc.stderr
    assert "a" * 40 in proc.stderr


def test_resolver_polls_only_while_producer_runs_then_returns_digest(tmp_path):
    responses = [
        {"status": "in_progress", "conclusion": None},
        {"status": "in_progress", "conclusion": None},
    ]
    with _ActionsApi(responses) as api:
        proc, calls = _stubbed_resolve(tmp_path, succeeds_on=3, api_url=api.url)

    assert proc.returncode == 0, proc.stderr
    assert calls == 3
    assert api.run_requests == 2
    assert proc.stdout.strip().endswith("@sha256:" + "a" * 64)


def test_alias_waits_for_live_source_producer_then_preserves_digest_invariants(tmp_path):
    _git(tmp_path, "init", "-q")
    _stage_alias_test_dockerignore(tmp_path)
    source = _commit(tmp_path, "backend", "cps/web.py", "WRITE = 1\n")
    subject = _commit(tmp_path, "docs", "docs/usage.md", "docs\n")
    responses = [
        {"status": "in_progress", "conclusion": None},
        {"status": "in_progress", "conclusion": None},
    ]
    with _ActionsApi(responses) as api:
        proc, log = _run_alias(
            tmp_path,
            source,
            subject,
            source_succeeds_on=3,
            api_url=api.url,
        )

    assert proc.returncode == 0, proc.stderr
    assert api.run_requests == 2
    assert f"-t registry.example/project/app:sha-{subject}" in log


@pytest.mark.parametrize("conclusion", ["cancelled", "failure"])
def test_alias_routes_terminal_source_producer_to_subject_rebuild(tmp_path, conclusion):
    """Observed terminal producers recover without manufacturing an alias."""
    _git(tmp_path, "init", "-q")
    _stage_alias_test_dockerignore(tmp_path)
    source = _commit(tmp_path, "backend", "cps/web.py", "WRITE = 1\n")
    subject = _commit(tmp_path, "docs", "docs/usage.md", "docs\n")
    recovery_output = tmp_path / "github-output"
    responses = [{"status": "completed", "conclusion": conclusion}]
    with _ActionsApi(responses) as api:
        proc, log = _run_alias(
            tmp_path,
            source,
            subject,
            source_succeeds_on=99,
            api_url=api.url,
            recovery_output=recovery_output,
        )

    assert proc.returncode == 0, proc.stderr
    assert api.run_requests == 1
    assert "https://github.example/runs/77" in proc.stderr
    assert f"concluded {conclusion}" in proc.stderr
    assert recovery_output.read_text(encoding="utf-8") == "recovery_build=true\n"
    assert log == ""


def test_resolver_stops_on_failed_job_before_live_run_concludes(tmp_path):
    responses = [
        {
            "status": "in_progress",
            "conclusion": None,
            "jobs": [
                {
                    "name": "Alias unchanged image to commit",
                    "status": "completed",
                    "conclusion": "failure",
                    "html_url": "https://github.example/runs/77/jobs/88",
                }
            ],
        }
    ]
    with _ActionsApi(responses) as api:
        proc, calls = _stubbed_resolve(tmp_path, succeeds_on=None, api_url=api.url)

    assert proc.returncode == 1
    assert calls == 1
    assert api.job_requests == 1
    assert "Alias unchanged image to commit" in proc.stderr
    assert "https://github.example/runs/77/jobs/88" in proc.stderr
    assert "run: https://github.example/runs/77" in proc.stderr
