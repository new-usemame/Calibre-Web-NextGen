#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Report whether the exact workflow run that publishes an E2E image is live.

Exit 0 only while the matching Build & Push run is queued or in progress.  A
terminal producer, a failed producer job, an absent run, or an unreadable API
all exit non-zero so callers never turn an unknown producer into a blind poll.
Callers that can safely rebuild the exact subject may request a distinct exit
code for terminal producers; unknown/absent API state still exits 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


WORKFLOW = "docker-image-build-dev.yml"
LIVE_STATUSES = frozenset({"queued", "in_progress", "pending", "requested", "waiting"})
FAILED_CONCLUSIONS = frozenset(
    {"action_required", "cancelled", "failure", "startup_failure", "timed_out"}
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--terminal-exit-code",
        type=int,
        default=1,
        help="exit code for a terminal run/job (default: 1)",
    )
    parser.add_argument("repository", help="GitHub owner/repository")
    parser.add_argument("sha", help="full producer commit SHA")
    parser.add_argument("event", choices=("push", "pull_request"))
    parser.add_argument("api_url", help="GitHub API base URL")
    return parser


def _get_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cwng-e2e-image-producer-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub Actions API request failed for {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub Actions API returned a non-object for {url}")
    return payload


def _error(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


def main() -> int:
    args = _parser().parse_args()
    if not 1 <= args.terminal_exit_code <= 125:
        return _error("terminal exit code must be between 1 and 125")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        return _error(f"invalid GitHub repository {args.repository!r}")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", args.sha):
        return _error("producer SHA must be a full commit ID")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return _error("GITHUB_TOKEN is required to inspect the image producer")

    api_url = args.api_url.rstrip("/")
    query = urllib.parse.urlencode(
        {"head_sha": args.sha, "event": args.event, "per_page": "10"}
    )
    runs_url = (
        f"{api_url}/repos/{args.repository}/actions/workflows/{WORKFLOW}/runs?{query}"
    )
    try:
        payload = _get_json(runs_url, token)
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            raise RuntimeError("GitHub Actions API response has no workflow_runs list")
        if not runs:
            return _error(
                f"no Build & Push producer run exists for {args.sha} ({args.event}); "
                f"queried {runs_url}"
            )

        run = runs[0]
        if not isinstance(run, dict):
            raise RuntimeError("GitHub Actions API returned a malformed workflow run")
        run_id = run.get("id", "unknown")
        run_url = str(run.get("html_url") or runs_url)
        status = str(run.get("status") or "unknown")
        conclusion = run.get("conclusion")

        if status == "completed":
            _error(
                f"Build & Push producer run {run_id} concluded {conclusion or 'without a verdict'} "
                f"but did not publish the required image: {run_url}"
            )
            return args.terminal_exit_code
        if status not in LIVE_STATUSES:
            return _error(
                f"Build & Push producer run {run_id} has unexpected status {status!r}: {run_url}"
            )

        jobs_url = str(run.get("jobs_url") or "")
        if not jobs_url:
            raise RuntimeError(f"Build & Push producer run {run_id} has no jobs_url")
        separator = "&" if "?" in jobs_url else "?"
        jobs_payload = _get_json(f"{jobs_url}{separator}per_page=100", token)
        jobs = jobs_payload.get("jobs")
        if not isinstance(jobs, list):
            raise RuntimeError("GitHub Actions API response has no jobs list")
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_conclusion = job.get("conclusion")
            if job.get("status") == "completed" and job_conclusion in FAILED_CONCLUSIONS:
                job_name = job.get("name") or job.get("id") or "unknown job"
                job_url = job.get("html_url") or run_url
                _error(
                    f"Build & Push producer job {job_name!r} concluded {job_conclusion} "
                    f"in run {run_id}: {job_url} (run: {run_url})"
                )
                return args.terminal_exit_code
    except RuntimeError as exc:
        return _error(str(exc))

    print(
        f"Build & Push producer run {run_id} is {status}; image may still publish: {run_url}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
