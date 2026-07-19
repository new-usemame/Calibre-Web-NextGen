# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Guard that `docker build .` works for someone with no credentials (#943).

Python and kepubify are mirrored into ghcr.io/new-usemame/pbs-cache so the image
build does not depend on the GitHub release CDN, which intermittently 404s the
Actions egress. That mirror package is private, and for a while the Dockerfile
made it a *hard* build requirement:

    FROM ghcr.io/new-usemame/pbs-cache:cpython-... AS pbs_mirror

so `docker build .` died at the first stage with `error from registry:
unauthorized` for anyone outside the org. A contributor could not build, and
therefore could not test — #940 is the concrete cost: a plausible-looking change
to the init script that would have broken fresh installs and every PUID != 911
install, submitted by someone who had no way to run it.

The fix keeps the mirror for CI and makes it opt-in. `PBS_SOURCE` selects the
source; it defaults to `upstream` (public release CDN, no credentials), and the
CI image builds pass `PBS_SOURCE=ghcr`. BuildKit only resolves stages the
selected target actually reaches, so a default build never touches the private
package.

These tests fail on the pre-fix Dockerfile (no PBS_SOURCE, mirror reachable by
default) and on the two regressions that would quietly undo the fix: flipping
the default back to `ghcr` (locks contributors out again), or dropping
`PBS_SOURCE=ghcr` from a CI build (silently returns that build to the flaky CDN).
"""

import re
from pathlib import Path

import pytest


# Pure file parsing, no Docker and no network — mark `unit` so CI's
# `pytest -m "smoke or unit"` selector actually collects the module.
pytestmark = pytest.mark.unit


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

PRIVATE_MIRROR = "ghcr.io/new-usemame/pbs-cache"

# Workflows that build the shipped image and therefore must pin the mirror.
IMAGE_BUILD_WORKFLOWS = (
    "docker-image-build-dev.yml",
    "docker-image-build-release.yml",
)


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return DOCKERFILE.read_text()


def _stage_bodies(text: str) -> dict[str, str]:
    """Map `AS <name>` stage name -> the text of that stage."""
    froms = list(re.finditer(r"^FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<name>\S+))?", text, re.MULTILINE))
    bodies: dict[str, str] = {}
    for i, m in enumerate(froms):
        if not m.group("name"):
            continue
        end = froms[i + 1].start() if i + 1 < len(froms) else len(text)
        bodies[m.group("name")] = text[m.start():end]
    return bodies


def test_pbs_source_defaults_to_credential_free_upstream(dockerfile_text: str) -> None:
    """`PBS_SOURCE` must be global and default to `upstream`.

    It has to be global because a FROM line can only interpolate a global ARG,
    and it has to default to `upstream` because the default is what a
    contributor's bare `docker build .` gets — that is the whole bug.
    """
    match = re.search(r"^ARG PBS_SOURCE=(\S+)", dockerfile_text, re.MULTILINE)
    assert match, "Dockerfile must declare `ARG PBS_SOURCE=<default>`"

    first_from = re.search(r"^FROM\b", dockerfile_text, re.MULTILINE)
    assert first_from and match.start() < first_from.start(), (
        "ARG PBS_SOURCE must be declared before the first FROM (global scope), "
        "otherwise the selector FROM lines cannot interpolate it."
    )
    assert match.group(1) == "upstream", (
        f"PBS_SOURCE defaults to {match.group(1)!r}, so a credential-free "
        f"`docker build .` resolves the PRIVATE {PRIVATE_MIRROR} package and "
        f"fails with `unauthorized` (#943). The default must be `upstream`; CI "
        f"opts into the mirror with PBS_SOURCE=ghcr."
    )


def test_mirror_is_selected_indirectly_not_hardwired(dockerfile_text: str) -> None:
    """The stages everything downstream COPYs from must be selectors, so the
    private mirror is only reachable when PBS_SOURCE=ghcr."""
    for stage in ("pbs", "kepubify"):
        assert re.search(
            rf"^FROM\s+{stage}_\$\{{PBS_SOURCE\}}\s+AS\s+{stage}_mirror\b",
            dockerfile_text,
            re.MULTILINE,
        ), (
            f"Expected `FROM {stage}_${{PBS_SOURCE}} AS {stage}_mirror`. Naming the "
            f"private mirror directly as {stage}_mirror makes it a hard build "
            f"requirement and locks contributors out (#943)."
        )


def test_default_build_reaches_no_private_mirror(dockerfile_text: str) -> None:
    """No stage reachable under the default (`upstream`) may reference the
    private package. Only the explicitly-opt-in `*_ghcr` stages may."""
    bodies = _stage_bodies(dockerfile_text)
    for stage in ("pbs_upstream", "kepubify_upstream"):
        assert stage in bodies, f"Missing credential-free `{stage}` stage"
        assert PRIVATE_MIRROR not in bodies[stage], (
            f"Stage {stage} references the private {PRIVATE_MIRROR}; it is the "
            f"fallback a contributor gets and must need no credentials."
        )

    referencing = {name for name, body in bodies.items() if PRIVATE_MIRROR in body}
    assert referencing <= {"pbs_ghcr", "kepubify_ghcr"}, (
        f"Only the opt-in *_ghcr stages may reference {PRIVATE_MIRROR}; found it "
        f"in {sorted(referencing - {'pbs_ghcr', 'kepubify_ghcr'})}."
    )


def test_both_sources_follow_the_same_version_pins(dockerfile_text: str) -> None:
    """The upstream fallback must download the versions the global pins name.

    If the two sources drift, contributors build against a different Python or
    kepubify than production ships, and their testing stops meaning anything —
    a subtler version of the same bug.
    """
    bodies = _stage_bodies(dockerfile_text)

    pbs = bodies["pbs_upstream"]
    assert "${PYTHON_BUILD_STANDALONE_RELEASE}" in pbs and "${PYTHON_VERSION}" in pbs, (
        "pbs_upstream must interpolate PYTHON_VERSION and "
        "PYTHON_BUILD_STANDALONE_RELEASE so it tracks the global pins."
    )
    assert "${KEPUBIFY_RELEASE}" in bodies["kepubify_upstream"], (
        "kepubify_upstream must interpolate KEPUBIFY_RELEASE so it tracks the global pin."
    )

    # The stages must re-declare the pins bare, or they inherit the empty string
    # and build a malformed URL (the #544 failure mode).
    for stage, args in (
        ("pbs_upstream", ("PYTHON_VERSION", "PYTHON_BUILD_STANDALONE_RELEASE")),
        ("kepubify_upstream", ("KEPUBIFY_RELEASE",)),
    ):
        for arg in args:
            assert re.search(rf"^ARG {arg}\s*$", bodies[stage], re.MULTILINE), (
                f"Stage {stage} must re-declare `ARG {arg}` (bare) to inherit the "
                f"global default; without it the download URL renders empty and 404s."
            )


def test_upstream_fallback_produces_what_downstream_copies(dockerfile_text: str) -> None:
    """The fallback stages must expose the exact paths the later stages COPY."""
    bodies = _stage_bodies(dockerfile_text)
    assert "/python.tar.gz" in bodies["pbs_upstream"], (
        "pbs_upstream must produce /python.tar.gz — that is what the dependencies "
        "stage COPYs from pbs_mirror."
    )
    assert "/kepubify" in bodies["kepubify_upstream"], (
        "kepubify_upstream must produce /kepubify."
    )
    # kepubify is COPYd straight to /usr/bin and executed, so it must be executable.
    assert "chmod 755 /kepubify" in bodies["kepubify_upstream"], (
        "kepubify_upstream must chmod 755 /kepubify; the GHCR mirror ships it "
        "already executable, so the fallback has to match or kepubify won't run."
    )


@pytest.mark.parametrize("workflow", IMAGE_BUILD_WORKFLOWS)
def test_ci_image_builds_pin_the_ghcr_mirror(workflow: str) -> None:
    """Every image-building workflow must pass PBS_SOURCE=ghcr.

    Dropping it does not fail the build — it silently sends CI back to the
    release CDN that 404d the Actions egress and broke every image build. This
    asserts one PBS_SOURCE=ghcr per build-args block so a newly added build job
    cannot quietly ship without it.
    """
    text = (WORKFLOWS / workflow).read_text()
    build_arg_blocks = text.count("build-args: |")
    assert build_arg_blocks, f"{workflow} has no build-args block to check"
    assert text.count("PBS_SOURCE=ghcr") == build_arg_blocks, (
        f"{workflow} has {build_arg_blocks} build-args block(s) but "
        f"{text.count('PBS_SOURCE=ghcr')} PBS_SOURCE=ghcr line(s). Every image "
        f"build must pin the mirror, or it falls back to the flaky release CDN."
    )
