# SPDX-License-Identifier: GPL-3.0-or-later
"""Dev builds on main: exactly one per IMAGE-RELEVANT merge, zero per ignored one.

The policy this file pins has two arms, and the filename is meant to be
read literally:

  * A merge to main whose paths are image-relevant — i.e. NOT wholly
    covered by docker-image-build-dev.yml's ``paths-ignore``
    (``**.md``, ``docs/**``, ``tests/**``, ``.github/**``) — must produce
    EXACTLY ONE ``:dev`` build, from the push trigger.
  * A merge confined to those ignored paths must produce ZERO builds,
    from any route. The ignore list is a deliberate decision that such
    changes cannot alter image contents (the Dockerfile is not under
    ``.github/``), so the second arm is not a gap in coverage; it is the
    optimization working. The invariant is "one when image-relevant,
    never when ignored" — NOT "every merge builds exactly once".

The defect this pins: ``update-translations.yml`` used to end with an
unconditional ``workflow_dispatch`` POST to ``docker-image-build-dev.yml``
on every run, which broke both arms. For an image-relevant merge, the dev
workflow's push trigger had already built the commit, so the dispatch
built the same SHA twice. For an ignored-paths merge that happened to run
the translations workflow — an edit to ``update-translations.yml`` itself
is the live example, since ``.github/**`` is ignored by the dev workflow
but listed in the translations workflow's own ``paths`` — the dispatch
force-built a commit the push trigger deliberately skipped, because
``workflow_dispatch`` has no ``paths-ignore``. Every one of those builds
pushed a new ``:dev`` image, which the operator's household server
auto-deploys via watchtower: a duplicate or unwanted build is a real
user-visible restart, not wasted CI minutes. Measured on the live Actions
log 2026-08-18: sha 2a644a04 got a push build at 15:25 and a dispatch
build at 15:27; sha a33ed5f9 (an "Update translations" commit) got three
builds for one logical change.

Why the dispatch existed, and why it became wrong: 218f2bc11 (2026-01-30)
added it — and simultaneously excluded main from the dev workflow's push
trigger — because the translation commit was pushed with
``GITHUB_TOKEN``. GitHub deliberately does not trigger workflows for
``GITHUB_TOKEN`` pushes, so back then the dispatch was the ONLY way main
ever got a dev image — correct code. Both premises later flipped:
d7e22c6fc (2026-05-02) moved the push to ``GH_PAT`` (a PAT push DOES
trigger workflows, so the translation commit has built itself since
then), and 4ce64df8e (2026-06-06) restored ``push: branches: [main]`` on
the dev workflow (so image-relevant merges build themselves). The
dispatch was never removed.

Rather than grep for the dispatch URL (a grep stays green on any rewrite
that double-builds by another route), these tests MODEL the trigger matrix
from the two workflow files as parsed YAML and count builds per commit:

  * the dev workflow's own ``push`` trigger (branches + paths-ignore),
  * the translations workflow's ``push`` paths filter,
  * any step in the translations workflow that hits the dev workflow's
    ``dispatches`` endpoint,
  * whether the translation commit's push self-triggers CI (PAT vs
    GITHUB_TOKEN) — the premise that decides whether a dispatch is needed
    at all.

Includes POSITIVE CONTROLS: docs-only merges must produce ZERO builds on
every revision of the workflows (so a harness that rejects everything
cannot masquerade as a working gate), and the dev workflow's push trigger
must still exist (so "fixing" the count by deleting the push trigger and
keeping only the dispatch goes red too).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - yaml ships with most distros
    yaml = None  # type: ignore[assignment]

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WF_DIR = REPO_ROOT / ".github" / "workflows"
DEV_WF = WF_DIR / "docker-image-build-dev.yml"
I18N_WF = WF_DIR / "update-translations.yml"


def _load(path: Path) -> dict:
    if yaml is None:
        pytest.skip("PyYAML not installed in this environment")
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _trigger(wf: dict) -> dict:
    """`on:` parsed as either the literal string 'on' or boolean True
    (YAML 1.1 quirk). Same convention as test_workflow_safety_invariants."""
    return wf.get("on") or wf.get(True) or {}


def _every_step(wf: dict):
    for job in (wf.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        for step in (job.get("steps") or []):
            if isinstance(step, dict):
                yield step


# ─── GitHub filter-pattern matching ──────────────────────────────────────
#
# GitHub matches `paths` / `paths-ignore` against the full repo-relative
# path; `*` does not cross `/`, `**` matches zero or more of ANY character
# (slash included), `?` matches one non-slash character. This is a
# deliberate simplification of minimatch, exact for the patterns these two
# workflows actually declare (`**.md`, `docs/**`, `cps/**`, exact files).


def _glob_to_regex(pattern: str) -> re.Pattern:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches_any(path: str, patterns: list) -> bool:
    return any(_glob_to_regex(str(p)).match(path) for p in patterns)


# ─── The trigger model ───────────────────────────────────────────────────


def _dev_push_trigger() -> dict:
    trig = _trigger(_load(DEV_WF))
    push = trig.get("push") or {}
    assert "main" in (push.get("branches") or []), (
        "docker-image-build-dev.yml no longer builds on push to main — the "
        "push trigger is the intended single build path per merge, and this "
        "whole model counts from it"
    )
    return push


def _dev_push_fires(changed_paths: list) -> bool:
    """The dev build's push trigger fires unless EVERY changed path is
    covered by paths-ignore."""
    ignores = _dev_push_trigger().get("paths-ignore") or []
    return any(not _matches_any(p, ignores) for p in changed_paths)


def _translations_runs(changed_paths: list) -> bool:
    push = (_trigger(_load(I18N_WF)).get("push")) or {}
    paths = push.get("paths") or []
    assert paths, "update-translations.yml lost its push paths filter — model stale"
    return any(_matches_any(p, paths) for p in changed_paths)


def _dispatch_steps() -> list:
    """Steps in update-translations.yml that force-run the dev build via the
    workflow_dispatch API. The correct count is zero."""
    wf = _load(I18N_WF)
    return [
        step
        for step in _every_step(wf)
        if "docker-image-build-dev.yml" in str(step.get("run") or "")
        and "dispatches" in str(step.get("run") or "")
    ]


def _translation_commit_paths() -> list:
    """The paths the commit step actually stages, parsed out of its
    `git add -f` line so the model cannot drift from the workflow. Glob
    pathspecs (`cps/translations/*/...`) are sampled with one concrete
    language — `*` cannot cross `/`, so any single segment matches the same
    ignore patterns."""
    wf = _load(I18N_WF)
    commit_step = next(
        step
        for step in _every_step(wf)
        if step.get("name") == "Commit translation updates"
    )
    m = re.search(r"git add(?:\s+-f)?\s+(.+)", str(commit_step.get("run") or ""))
    assert m, "could not find the git add line in the translation commit step"
    pathspecs = m.group(1).split()
    assert pathspecs, "git add line names no pathspecs"
    return [spec.replace("*", "de") for spec in pathspecs]


def _translation_push_self_triggers_ci() -> bool:
    """True while the translation commit rides a PAT, because a PAT push
    triggers workflows and a GITHUB_TOKEN push deliberately does not.

    This is the premise that makes a manual dispatch redundant. It was
    FALSE when the dispatch was added (218f2bc11, GITHUB_TOKEN push) and
    has been TRUE since the workflow moved to GH_PAT (d7e22c6fc, with the
    commit step additionally gated on the PAT being available). If the
    project ever falls back to a GITHUB_TOKEN push, this goes red and the
    translation commit stops reaching :dev on its own — THAT is the only
    shape in which a dispatch belongs back in this workflow.
    """
    wf = _load(I18N_WF)
    checkout = next(
        (s for s in _every_step(wf) if str(s.get("uses") or "").startswith("actions/checkout")),
        None,
    )
    assert checkout is not None, "update-translations.yml has no checkout step"
    token = str((checkout.get("with") or {}).get("token") or "")
    persists = str((checkout.get("with") or {}).get("persist-credentials", "")).lower()
    commit_step = next(
        step
        for step in _every_step(wf)
        if step.get("name") == "Commit translation updates"
    )
    pushes = "git push" in str(commit_step.get("run") or "")
    return "GH_PAT" in token and persists == "true" and pushes


def _builds_of_merge_commit(changed_paths: list, translation_committed: bool) -> int:
    builds = 1 if _dev_push_fires(changed_paths) else 0
    if _translations_runs(changed_paths) and not translation_committed:
        # With nothing committed, a dispatch targets github.sha — the merge
        # itself, which the push trigger already built (or deliberately
        # skipped, for paths-ignore'd changes).
        builds += len(_dispatch_steps())
    return builds


def _builds_of_translation_commit(changed_paths: list, translation_committed: bool) -> int:
    if not (_translations_runs(changed_paths) and translation_committed):
        return 0  # no such commit exists in this scenario
    builds = 0
    if _translation_push_self_triggers_ci() and _dev_push_fires(_translation_commit_paths()):
        builds += 1  # the PAT push triggers the dev workflow like any push
    # A dispatch in the committed case targets the translation SHA — the
    # commit the push trigger just built.
    builds += len(_dispatch_steps())
    return builds


# ─── The two-arm policy, over the trigger matrix ─────────────────────────
#
# (changed paths on the merge, translations committed?, want builds of the
# merge commit, want builds of the generated translation commit)
_SCENARIOS = [
    # The everyday merge. One push build, nothing else.
    (["cps/web.py"], False, 1, 0),
    # A merge whose run also lands a generated translation commit: each of
    # the two commits gets exactly one build from its own push.
    (["cps/web.py"], True, 1, 1),
    # A translations-only human PR: the workflow runs but finds nothing to
    # commit. Still exactly one build — of the merge.
    (["cps/translations/de/LC_MESSAGES/messages.po"], False, 1, 0),
    # The translation machinery itself is image content (scripts/ is not in
    # paths-ignore), so this merge must be built — once.
    (["scripts/update_translations.sh"], False, 1, 0),
    # ZERO ARM + POSITIVE CONTROL: docs-only merges are deliberately skipped
    # by the dev workflow's paths-ignore, and the translations workflow does
    # not run at all (its paths don't match). Zero builds, on every revision
    # of these workflows — a harness that rejected everything would go red
    # here.
    (["docs/usage.md"], False, 0, 0),
    (["README.md"], False, 0, 0),
    # The zero arm's live case: an edit to the translations workflow ITSELF
    # runs the workflow (its own file is in its paths) but lies wholly
    # inside the dev workflow's ignored paths, so the push trigger
    # deliberately skips it. The removed dispatch force-built exactly these
    # commits. Zero builds is the intended, permanent behaviour for this
    # class — the merge of the PR that introduced this test is itself such
    # an event (it touches only .github/ and tests/).
    ([".github/workflows/update-translations.yml"], False, 0, 0),
]


@pytest.mark.parametrize(
    "changed,committed,want_merge,want_translation",
    _SCENARIOS,
    ids=[
        "code-push:1-build",
        "code-push-with-translation-commit:1-build-each",
        "translations-only-pr:1-build",
        "translation-script-change:1-build",
        "docs-only-md:0-builds(control)",
        "root-readme-only:0-builds(control)",
        "workflow-self-edit:0-builds",
    ],
)
def test_image_relevant_merges_build_exactly_once_ignored_merges_build_never(
    changed, committed, want_merge, want_translation
):
    """Both arms at once: image-relevant merges build exactly once, merges
    confined to the dev workflow's paths-ignore build never — and nothing
    outside the push trigger (a dispatch, a re-run) may add a build either
    arm didn't ask for."""
    merge_builds = _builds_of_merge_commit(changed, committed)
    translation_builds = _builds_of_translation_commit(changed, committed)
    assert merge_builds == want_merge, (
        f"merge touching {changed} gets {merge_builds} dev builds, want {want_merge}. "
        "Every redundant build pushes a new :dev image that watchtower "
        "auto-deploys on the operator's household server — a user-visible "
        "restart per duplicate."
    )
    assert translation_builds == want_translation, (
        f"the generated translation commit gets {translation_builds} dev builds, "
        f"want {want_translation}. Its GH_PAT push already triggers the dev "
        "workflow's push rule like any other push to main; a dispatch on top "
        "builds the same SHA a second time."
    )


def test_no_step_dispatches_the_dev_build():
    """The direct pin, kept alongside the matrix for the crisp failure
    message: update-translations.yml contains no step that POSTs to the dev
    workflow's dispatches endpoint."""
    offenders = [str(s.get("name") or s.get("run", "")[:60]) for s in _dispatch_steps()]
    assert not offenders, (
        "update-translations.yml still force-dispatches docker-image-build-dev.yml "
        f"from step(s) {offenders}. The push trigger on main already builds every "
        "image-relevant merge exactly once, and the GH_PAT-pushed translation "
        "commit self-triggers — the dispatch only ever builds a SHA that was "
        "already built or deliberately skipped."
    )


def test_no_dead_dispatch_plumbing_remains():
    """The env plumbing existed only to feed the dispatch. Leaving it invites
    the next reader to invent a new consumer; the workflow must not reference
    it at all."""
    text = I18N_WF.read_text(encoding="utf-8")
    for token in ("DEV_BUILD_SHA", "TRANSLATION_COMMIT_SHA", "TRANSLATION_COMMITTED"):
        assert token not in text, (
            f"{token} is still referenced in update-translations.yml but has no "
            "consumer now that the dispatch is gone — remove the plumbing with "
            "the dispatch"
        )


def test_no_actions_write_permission_without_the_dispatch():
    """`actions: write` existed solely so the dispatch POST could authenticate
    with GITHUB_TOKEN. With the dispatch gone the grant is an unexplained
    escalation; the exemption in test_workflow_safety_invariants.py was
    removed with it, so re-granting fails there too."""
    wf = _load(I18N_WF)
    perms = wf.get("permissions") or {}
    assert isinstance(perms, dict), "update-translations.yml lost its permissions block"
    assert perms.get("actions") != "write", (
        "update-translations.yml still grants actions: write, which only the "
        "removed dispatch needed. Narrow the token with the dispatch."
    )


def test_translation_commit_push_self_triggers_ci_premise():
    """Tripwire on the premise, not the mechanism: the dispatch is redundant
    ONLY while the translation commit is pushed with a PAT. If the workflow
    ever falls back to a GITHUB_TOKEN push, the generated commit no longer
    triggers CI at all (GitHub suppresses GITHUB_TOKEN pushes by design) and
    .po changes stop reaching :dev. That day — and only that day — a
    dispatch or an equivalent trigger belongs back in this workflow, and
    this test is where that decision gets recorded."""
    assert _translation_push_self_triggers_ci(), (
        "the translation commit no longer self-triggers CI (checkout is not "
        "persisting a PAT, or the push is gone). With no dispatch step, "
        "generated translation commits now reach :dev via nothing — either "
        "restore the PAT push or consciously re-add a trigger."
    )


def test_dev_workflow_keeps_its_push_trigger_and_paths_ignore():
    """Architecture pin for the matrix: the push trigger on main is the
    intended single build path. Deleting it and keeping only the dispatch
    would also produce 'one build per merge' on code pushes — while breaking
    paths-ignore, non-main dev builds, and the docs-only skip. The count
    alone must not bless that rewrite."""
    push = _dev_push_trigger()
    ignores = push.get("paths-ignore") or []
    assert ignores, (
        "docker-image-build-dev.yml lost its paths-ignore list — docs-only "
        "merges would pay for a full image build again"
    )
    for pattern in ("**.md", "docs/**", "tests/**", ".github/**"):
        assert pattern in ignores, f"paths-ignore lost {pattern!r}"
