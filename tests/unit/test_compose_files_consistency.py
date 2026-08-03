# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Contract tests for the two shipped compose templates.

``docker-compose.yml`` is what a user copies to run the app;
``docker-compose.yml.dev`` is what a contributor copies to develop it
(``CONTRIBUTING.md`` line 42: ``cp docker-compose.yml.dev docker-compose.override.yml``).
Neither is executed by CI, so nothing else in the tree notices when one drifts.
Three drifts have actually happened, and this module pins each.

**Drift 1 — the dev file pointing at another project's image.** Until #1337 the
dev template pulled ``crocodilestick/calibre-web-automated:latest``. Anyone
following ``CONTRIBUTING.md`` was therefore developing against upstream's
release, not this fork's code: local edits bind-mounted over a foreign image,
with none of this fork's ~100 divergent patches underneath. The failure is
silent — the container starts and serves a working app, just not *this* app.

**Drift 2 — colliding container names.** ``CONTRIBUTING.md`` starts the dev file
standalone (``docker compose -f docker-compose.override.yml up -d``), not as a
merged override, so its ``container_name`` is used verbatim. If it equals the
production one, a contributor who also runs a normal instance gets
``Conflict. The container name ... is already in use`` and has to diagnose it.
The names must stay distinct.

**Drift 3 — a comment that names this image as the upstream one.** #1337
find-replaced ``crocodilestick/calibre-web-automated`` inside the sentence
"Upstream image still pullable at X if you prefer to track upstream", which left
both files pointing at *their own* ``image:`` line and calling it upstream. A
reader following that advice learns the opposite of the truth. The whole point
of the sentence was to name a *different* image than the one configured, so any
recurrence is the same bug regardless of wording.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.yml.dev"

OUR_IMAGE = "ghcr.io/new-usemame/calibre-web-nextgen"


def _service(path: Path) -> dict:
    """Return the single service definition from a compose template."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = doc["services"]
    assert len(services) == 1, f"{path.name}: expected exactly one service, got {sorted(services)}"
    return next(iter(services.values()))


@pytest.mark.parametrize("path", [PROD_COMPOSE, DEV_COMPOSE], ids=["prod", "dev"])
def test_compose_template_exists_and_parses(path: Path) -> None:
    assert path.is_file(), f"{path.name} is shipped and referenced by docs; it must exist"
    _service(path)


@pytest.mark.parametrize("path", [PROD_COMPOSE, DEV_COMPOSE], ids=["prod", "dev"])
def test_image_is_this_fork(path: Path) -> None:
    """Drift 1. Both templates must run *this* fork's image."""
    image = _service(path)["image"]
    repo = image.rsplit(":", 1)[0]
    assert repo == OUR_IMAGE, (
        f"{path.name} pulls {image!r}. Both compose templates must run this fork's image — "
        f"a contributor bind-mounting local code over a different project's image gets a "
        f"working container that is not this application."
    )


def test_dev_template_tracks_main() -> None:
    """The dev template follows main, not the last public release.

    ``:dev`` is rebuilt and pushed on every merge to main (release policy); ``:latest``
    only moves when a version is published, which can be a day or more behind the code a
    contributor is working against.
    """
    assert _service(DEV_COMPOSE)["image"].rsplit(":", 1)[1] == "dev"


def test_prod_template_is_not_pinned_to_a_stale_tag() -> None:
    """The user-facing template should track releases rather than freeze on one.

    A hard-coded ``:vX.Y.Z`` here silently strands every new user on whatever version
    happened to be current when the line was last edited.
    """
    assert _service(PROD_COMPOSE)["image"].rsplit(":", 1)[1] == "latest"


def test_container_names_do_not_collide() -> None:
    """Drift 2. CONTRIBUTING.md runs the dev file standalone, so the names are used as written."""
    prod_name = _service(PROD_COMPOSE)["container_name"]
    dev_name = _service(DEV_COMPOSE)["container_name"]
    assert prod_name != dev_name, (
        f"both templates declare container_name={prod_name!r}. CONTRIBUTING.md starts the dev "
        f"file with `docker compose -f ... up -d`, so a contributor running a production "
        f"instance too hits a container-name conflict."
    )


@pytest.mark.parametrize("path", [PROD_COMPOSE, DEV_COMPOSE], ids=["prod", "dev"])
def test_no_comment_calls_our_own_image_upstream(path: Path) -> None:
    """Drift 3. A sentence about "upstream" must not resolve to this fork's own image.

    Checks the comment text only, so it cannot be satisfied by deleting the ``image:``
    line, and does not care about the exact wording.
    """
    comments = [
        line.split("#", 1)[1]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("#")
    ]
    for comment in comments:
        if re.search(r"\bupstream\b", comment, re.IGNORECASE) and OUR_IMAGE in comment:
            pytest.fail(
                f"{path.name}: comment describes {OUR_IMAGE} as upstream: {comment.strip()!r}. "
                f"Upstream is a different project; naming this fork's own image there tells the "
                f"reader the opposite of the truth."
            )
