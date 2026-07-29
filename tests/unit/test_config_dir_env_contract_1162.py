# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #1162 — migration markers written to the read-only app dir.

Reported by @auspex: every ingest of a book logged five permission-denied
warnings, all of the shape::

    [kobo-unique-migration] could not write marker
    /app/calibre-web-automated/.cwa_migrations/kobo_unique_constraints_v1:
    [Errno 13] Permission denied

He also spotted the tell that identifies the root cause: there is no
``/app/calibre-web-automated/.cwa_migrations`` directory, but there *is* a
``/config/.cwa_migrations``. So the same migrations resolve to two different
directories depending on which process runs them.

Why: ``cps.constants.CONFIG_DIR`` is ``os.environ.get('CALIBRE_DBPATH',
BASE_DIR)`` — it falls back to the *source tree*. Three other resolvers in this
codebase (``cps/calibre_init.py``, ``scripts/ingest_processor.py``,
``cps/duplicate_notice.py``) fall back to ``/config`` instead. Those two
defaults disagree, and which one you get depends entirely on whether
``CALIBRE_DBPATH`` happens to be exported in your process.

It was exported in exactly two of the eleven s6 run scripts — ``cwa-init`` and
``svc-calibre-web-automated``. The ingest service was not one of them, and
``scripts/ingest_processor.py`` calls ``ub.init_db()``, which runs
``migrate_Database()`` and every marker-writing migration under it. So ingest
ran the migrations against ``BASE_DIR``, could not write there (the app tree
ships owned by the build user, not the runtime user), and re-ran all five
migrations on every single ingest.

The fix sets ``CALIBRE_DBPATH=/config`` once, as a Docker ``ENV`` in the runtime
stage. Every s6 run script uses ``#!/usr/bin/with-contenv bash``, so all of them
inherit it — including any service added later. The per-service ``export``
lines are left in place as belt-and-braces.

These tests pin the contract rather than the fix, so the guard survives a
refactor: whatever mechanism supplies it, every Python-running service must
resolve ``CONFIG_DIR`` to the config volume.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
S6_ROOT = REPO_ROOT / "root" / "etc" / "s6-overlay" / "s6-rc.d"

CONFIG_VOLUME = "/config"
ENV_VAR = "CALIBRE_DBPATH"


def _dockerfile_env_value(var: str) -> str | None:
    """Return the value of the last `ENV <var>=<value>` in the Dockerfile.

    Only the final build stage matters for runtime, and the last assignment
    wins, so scanning the whole file and taking the last hit is correct.
    Handles `ENV k=v`, `ENV k v`, and quoted values.
    """
    if not DOCKERFILE.is_file():
        pytest.fail(f"Dockerfile not found at {DOCKERFILE}")
    found = None
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.upper().startswith("ENV "):
            continue
        body = line[4:].strip()
        m = re.match(rf"^{re.escape(var)}\s*=\s*(.+)$", body) or re.match(
            rf"^{re.escape(var)}\s+(.+)$", body
        )
        if m:
            found = m.group(1).strip().strip('"').strip("'")
    return found


def _services_with_run_scripts() -> list[Path]:
    if not S6_ROOT.is_dir():
        pytest.fail(f"s6 service root not found at {S6_ROOT}")
    return sorted(p for p in S6_ROOT.glob("*/run") if p.is_file())


def _invokes_python(run_script: Path) -> bool:
    text = run_script.read_text(encoding="utf-8", errors="ignore")
    return bool(re.search(r"\bpython[0-9.]*\b|\.py\b", text))


# --------------------------------------------------------------------------
# The bug, stated as behaviour: CONFIG_DIR must not fall back to the app tree.
# --------------------------------------------------------------------------


def test_config_dir_follows_the_env_var_when_set(monkeypatch, tmp_path):
    """CONFIG_DIR honours CALIBRE_DBPATH — this is the mechanism the fix uses."""
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    import importlib

    import cps.constants as constants

    importlib.reload(constants)
    try:
        assert constants.CONFIG_DIR == str(tmp_path)
    finally:
        monkeypatch.delenv(ENV_VAR, raising=False)
        importlib.reload(constants)


def test_config_dir_falls_back_to_the_source_tree_without_the_env_var(monkeypatch):
    """Documents the trap: unset, CONFIG_DIR is BASE_DIR — the read-only app tree.

    This is why the ENV must be set globally rather than per-service. If this
    ever stops being true the container fix is belt-and-braces rather than
    load-bearing, and this test should be revisited deliberately, not deleted.
    """
    monkeypatch.delenv(ENV_VAR, raising=False)
    import importlib

    import cps.constants as constants

    importlib.reload(constants)
    assert constants.CONFIG_DIR == constants.BASE_DIR
    assert not constants.CONFIG_DIR.startswith(CONFIG_VOLUME)


# --------------------------------------------------------------------------
# The fix + the drift guard.
# --------------------------------------------------------------------------


def test_dockerfile_sets_the_config_dir_env_globally():
    """RED on main: the Dockerfile set no ENV at all, so 9 of 11 services
    resolved CONFIG_DIR to the app tree."""
    value = _dockerfile_env_value(ENV_VAR)
    assert value is not None, (
        f"Dockerfile does not set ENV {ENV_VAR}. Without it, every s6 service "
        f"that does not export it by hand resolves cps.constants.CONFIG_DIR to "
        f"BASE_DIR (the read-only app tree) — that is #1162."
    )
    assert value == CONFIG_VOLUME, (
        f"ENV {ENV_VAR} is {value!r}, expected {CONFIG_VOLUME!r} — the config volume."
    )


def test_every_python_running_service_resolves_config_dir_to_the_volume():
    """The real drift guard.

    A service added later must not silently reintroduce #1162. It's satisfied
    either by the global Dockerfile ENV or by its own export, so this stays true
    however the contract is supplied.
    """
    global_env_ok = _dockerfile_env_value(ENV_VAR) == CONFIG_VOLUME

    offenders = []
    for run_script in _services_with_run_scripts():
        if not _invokes_python(run_script):
            continue
        exports_locally = ENV_VAR in run_script.read_text(encoding="utf-8", errors="ignore")
        if not (global_env_ok or exports_locally):
            offenders.append(run_script.parent.name)

    assert not offenders, (
        f"These s6 services run Python but have no {ENV_VAR} in scope: "
        f"{sorted(offenders)}. They will write migration markers and other "
        f"CONFIG_DIR-derived state into the read-only app tree (#1162)."
    )


def test_run_scripts_use_with_contenv_so_they_inherit_the_docker_env():
    """The global ENV only reaches a service if its run script imports the
    container environment. If a future run script drops `with-contenv`, the
    Dockerfile ENV silently stops applying to it — catch that here."""
    offenders = []
    for run_script in _services_with_run_scripts():
        if not _invokes_python(run_script):
            continue
        first_line = run_script.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        if "with-contenv" not in first_line:
            offenders.append(f"{run_script.parent.name}: {first_line!r}")

    assert not offenders, (
        "These Python-running s6 services do not use with-contenv, so they do "
        f"not inherit the Dockerfile's {ENV_VAR}: {offenders}"
    )


def test_runtime_migration_markers_are_excluded_from_the_build_context():
    """Adjacent hardening found while verifying #1162.

    The local image had five .cwa_migrations markers baked into its /app layer,
    because the documented local-dev setup mounts /config at the repo root and
    .dockerignore did not exclude it. A marker present on a *fresh* install
    makes the migration it gates get skipped entirely — the install silently
    never runs it. CI builds from a clean checkout so released images were
    never affected; this keeps local builds honest too.
    """
    dockerignore = REPO_ROOT / ".dockerignore"
    assert dockerignore.is_file(), ".dockerignore is missing"
    entries = {
        line.strip().rstrip("/")
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert ".cwa_migrations" in entries, (
        ".dockerignore must exclude .cwa_migrations/ so a developer's runtime "
        "markers can't be baked into an image and skip migrations on a fresh "
        "install."
    )


def test_ingest_path_is_the_one_that_runs_the_marker_migrations():
    """Pins the actual reporter-facing call chain.

    scripts/ingest_processor.py calls ub.init_db(), which calls
    migrate_Database(), which runs the five marker-writing migrations. That is
    why @auspex saw the warnings on *ingest* rather than at boot. If this call
    moves, the service-env contract above needs re-checking against wherever it
    moved to.
    """
    ingest = REPO_ROOT / "scripts" / "ingest_processor.py"
    assert "ub.init_db(" in ingest.read_text(encoding="utf-8"), (
        "ingest_processor.py no longer calls ub.init_db(); re-verify which "
        "services trigger migrate_Database() and that each has CALIBRE_DBPATH."
    )

    ub = (REPO_ROOT / "cps" / "ub.py").read_text(encoding="utf-8")
    assert "migrate_Database(session)" in ub
    assert ".cwa_migrations" in ub
