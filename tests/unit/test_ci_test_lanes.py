# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""fork #1105: every test must run in some CI job, or say why it doesn't.

The Fast Tests gate selects with ``-m "smoke or unit"``. Marker selection is
opt-in, so before this landed a file that forgot ``@pytest.mark.unit`` was
collected and then deselected — it ran nowhere, and the gate stayed green while
it did. 118 of 434 files under ``tests/unit/`` had drifted out that way, hiding
937 tests; ``tests/smoke/`` had another 36. Among them were regression guards
for shipped fixes, and 37 tests that had gone red unnoticed.

The lane now comes from the directory, so a new file cannot be born invisible.
These tests defend the three ways that could quietly stop being true:

* a test directory appears that no lane covers;
* the workflow's ``-m`` expression drifts away from the lanes conftest assigns;
* a file opts itself out by declaring a slower lane.

Plus the end-to-end check that the pieces actually compose: collect a
previously-invisible file with the gate's own selector and see nothing dropped.
"""

import pathlib
import re
import shlex
import subprocess
import sys

import pytest

#: Declared explicitly rather than inherited from the directory. This module is
#: the guard for the directory-lane mechanism, so it must not depend on that
#: mechanism to be selected — if the lane assignment regressed, an inherited
#: marker would take these tests down with it and the regression would report
#: nothing at all.
pytestmark = pytest.mark.unit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tests.conftest import LANE_BY_DIRECTORY, LANE_MARKERS, lane_for_path  # noqa: E402
from tests.quarantine import QUARANTINED  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"
WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"

#: Directories under tests/ that deliberately run outside the fast gate. Each
#: needs a reason, because "not in the fast gate" is how tests disappear.
LANELESS_DIRECTORIES = {
    "docker": "docker_integration/docker_e2e lanes, run by the Integration job",
    "integration": "integration lane, run by the Integration job",
    "fixtures": "sample data, not tests",
    "__pycache__": "build artefact",
}

#: Files under a fast-lane directory that declare a slower lane, and why. A
#: file added here leaves the Fast Tests gate, so it should be a visible diff.
SLOW_LANE_OPT_OUTS = {
    "tests/unit/test_book_format_checksums_table_creation.py":
        "one class declares `integration` to stay out of the xdist pool, because "
        "cps.progress_syncing.models imports cps.db and cps.ub at module level "
        "and hangs in a worker subprocess "
        "(notes/xdist-worker-ipc-hang-followup-2026-05-21.md). Run serially by "
        "the Fast Tests job's 'non-xdist unit tests' step.",
}

#: An opt-out only tells you a file left the fast gate. It does not tell you any
#: OTHER job picked it up, and for two files nothing did: the Integration job
#: selects by PATH (``pytest tests/docker/ tests/integration/``), so a file under
#: tests/unit/ declaring `slow` or `integration` ran in NEITHER job.
#: ``test_every_lane_opt_out_is_actually_run_somewhere`` below closes that by
#: executing the workflow's own invocations against each opt-out file.


def _fast_gate_marker_expression():
    """Return the -m expression the Fast Tests job selects with."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r'pytest -m "([^"]+)"', workflow)
    assert match, (
        "could not find the Fast Tests `pytest -m \"...\"` invocation in %s — "
        "if the gate moved, this guard has to follow it" % WORKFLOW.name
    )
    return match.group(1)


def _workflow_step_block(step_name):
    """Return one named workflow step without requiring a YAML dependency."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    marker = "      - name: %s\n" % step_name
    assert marker in workflow, "workflow step %r is missing" % step_name
    return workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]


@pytest.mark.parametrize(
    "step_name",
    [
        "Run smoke and unit tests",
        "Run non-xdist unit tests (lane opt-outs)",
        "Run Docker integration tests",
    ],
)
def test_ci_pytest_steps_report_skip_reasons(step_name):
    """A green count is insufficient: CI must name every skip and its reason."""
    block = re.sub(r"\\\s*\n\s*", " ", _workflow_step_block(step_name))
    commands = [line.strip() for line in block.splitlines()
                if line.strip().startswith("pytest ")]
    assert len(commands) == 1, "%s must contain one pytest command" % step_name
    assert "-rs" in shlex.split(commands[0]), (
        "%s must pass -rs so its job log names every skipped test and reason"
        % step_name
    )


def test_every_test_directory_has_a_lane_or_a_stated_reason():
    directories = {
        path.parent.name
        for path in TESTS.rglob("test_*.py")
        if path.parent != TESTS
    }
    unaccounted = directories - set(LANE_BY_DIRECTORY) - set(LANELESS_DIRECTORIES)
    assert not unaccounted, (
        "tests/%s holds tests that no CI lane claims — they will be collected "
        "and silently deselected by every job. Map the directory in "
        "conftest.LANE_BY_DIRECTORY, or record why it runs elsewhere in "
        "LANELESS_DIRECTORIES (#1105)" % sorted(unaccounted)
    )


def test_no_test_file_sits_at_the_tests_root():
    """A file directly under tests/ inherits no directory, so it has no lane.

    `tests/test_simple_ingest.py` lived here: the fast gate deselected it for
    having no marker, and the Integration job names `tests/docker/` and
    `tests/integration/` explicitly, so it never named it either. It ran in no
    job at all — the same hole as #1105, one level up, and invisible to the
    directory check above.
    """
    stray = sorted(path.name for path in TESTS.glob("test_*.py"))
    assert not stray, (
        "tests/%s sit at the tests/ root, where no directory implies a lane and "
        "no CI job's paths reach them. Move each into the directory for the job "
        "that should run it (#1105)" % stray
    )


def test_fast_gate_selects_the_lanes_conftest_assigns():
    expression = _fast_gate_marker_expression()
    selected = set(re.findall(r"[a-z_]+", expression)) - {"or", "and", "not"}
    assigned = set(LANE_BY_DIRECTORY.values())
    assert assigned <= selected, (
        "conftest assigns the lanes %s but the Fast Tests gate selects %r — "
        "tests in the unselected lanes would run in no job at all (#1105)"
        % (sorted(assigned - selected), expression)
    )


def test_fast_lane_files_do_not_silently_opt_out():
    opted_out = {}
    slow_markers = LANE_MARKERS - {"unit", "smoke"}
    for directory in LANE_BY_DIRECTORY:
        for path in sorted((TESTS / directory).glob("test_*.py")):
            source = path.read_text(encoding="utf-8")
            declared = set(re.findall(r"pytest\.mark\.([a-z_]+)", source))
            if declared & slow_markers:
                opted_out[str(path.relative_to(REPO))] = sorted(declared & slow_markers)

    assert set(opted_out) == set(SLOW_LANE_OPT_OUTS), (
        "the set of fast-lane files declaring a slower lane changed: %r. A file "
        "that opts out stops running in the Fast Tests gate, so record it in "
        "SLOW_LANE_OPT_OUTS with the reason (#1105)" % opted_out
    )


@pytest.mark.parametrize(
    "path, expected",
    [
        ("tests/unit/test_anything.py", "unit"),
        ("tests/smoke/test_anything.py", "smoke"),
        ("tests/integration/test_anything.py", None),
        ("tests/test_top_level.py", None),
        ("/somewhere/else/test_anything.py", None),
    ],
)
def test_lane_is_derived_from_the_directory(path, expected):
    candidate = path if path.startswith("/") else str(REPO / path)
    assert lane_for_path(candidate) == expected


def test_a_declared_lane_is_never_overridden_by_the_directory():
    """The deliberately-slow files under tests/unit/ must stay out of the gate.

    Asserted by collecting them under the gate's real selector rather than by
    reading their source: a hook changed to add the directory lane
    unconditionally would still leave the source text saying `integration`, so
    a source-level check would pass while the files quietly joined Fast Tests.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *sorted(SLOW_LANE_OPT_OUTS),
         "-m", _fast_gate_marker_expression(),
         "--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO)},
    )
    assert "deselected" in result.stdout, (
        "files that declare a slower lane were pulled into the Fast Tests gate "
        "by their directory — a declared lane must win (#1105):\n%s"
        % result.stdout[-2000:]
    )


def test_capability_markers_do_not_imply_a_fast_lane():
    """`requires_docker`/`requires_calibre` say what a test needs, not how fast.

    They are not in LANE_MARKERS, so a fast-lane file carrying only one of them
    would be auto-marked `unit` and would execute in Fast Tests on a runner that
    satisfies the requirement — without anyone having claimed it is a fast,
    isolated unit test. No file does this today; this keeps it that way.
    """
    capability_markers = {"requires_docker", "requires_calibre"}
    offenders = {}
    for directory in LANE_BY_DIRECTORY:
        for path in sorted((TESTS / directory).glob("test_*.py")):
            declared = set(re.findall(
                r"pytest\.mark\.([a-z_]+)", path.read_text(encoding="utf-8")))
            if declared & capability_markers and not declared & LANE_MARKERS:
                offenders[str(path.relative_to(REPO))] = sorted(
                    declared & capability_markers)

    assert not offenders, (
        "these files declare an environment requirement but no lane, so the "
        "directory would classify them as fast unit tests: %r. Declare the lane "
        "the test actually belongs to (#1105)" % offenders
    )


def test_every_quarantined_test_names_an_issue():
    missing = sorted(
        node for node, reason in QUARANTINED.items()
        if not re.search(r"#\d+", reason)
    )
    assert not missing, (
        "quarantined tests must name the issue that tracks fixing them, "
        "otherwise the entry is just a test that stopped running: %r" % missing
    )


def test_quarantined_tests_still_exist():
    """A stale entry silently protects nothing and hides that it's stale."""
    for node in QUARANTINED:
        relative = node.split("::", 1)[0]
        assert (REPO / relative).exists(), (
            "%s is quarantined but its file is gone — drop the entry (#1105)"
            % node
        )


def test_stubbing_the_cps_package_does_not_leak_into_the_next_file():
    """A test that fakes ``cps`` in sys.modules must put it back.

    Bringing the invisible files into the gate put them in a worker process
    with everything else for the first time. ``test_calibre_init.py`` stubs the
    whole ``cps`` package and used to leave the stub behind, so the next file to
    do ``from cps import config`` got ``ImportError: ... (unknown location)`` —
    a failure that lands on an innocent file and moves with worker scheduling.

    Ordered deliberately: the stubbing file runs first.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/unit/test_calibre_init.py",
         "tests/unit/test_ingest_config_full_load.py",
         "-m", _fast_gate_marker_expression(),
         "-q", "--no-header", "-p", "no:cacheprovider", "--timeout=120"],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO)},
    )
    assert result.returncode == 0, (
        "a stubbed sys.modules['cps'] leaked out of the file that installed it "
        "and broke a later file (#1105):\n%s" % result.stdout[-3000:]
    )


def test_previously_invisible_file_is_selected_by_the_fast_gate():
    """End-to-end: the gate's own selector must drop nothing from tests/unit.

    Collecting the whole suite costs ~2 minutes, so this samples files that
    were among the 118 unmarked ones. It asserts the outcome (nothing
    deselected), not the mechanism, so it keeps passing if someone later adds
    explicit markers to them.
    """
    samples = [
        "tests/unit/test_320_reorder_legacy_glyph.py",
        "tests/unit/test_1089_firefox_scrollbar_overlay.py",
        "tests/unit/test_annotation_schema.py",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *samples,
         "-m", _fast_gate_marker_expression(),
         "--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO)},
    )
    assert "deselected" not in result.stdout, (
        "the Fast Tests selector still drops tests from tests/unit/ — the lane "
        "assignment in conftest is not reaching them (#1105):\n%s"
        % result.stdout[-2000:]
    )
    assert " tests collected" in result.stdout or " test collected" in result.stdout, (
        "collection produced no tests, so this guard proves nothing:\n%s"
        % result.stdout[-2000:]
    )


def _workflow_pytest_invocations():
    """Every ``pytest`` command the workflow runs, as (paths, marker expression).

    Shell line continuations are joined first, so an invocation split across a
    dozen lines with trailing backslashes is read as the single command it is.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    joined = re.sub(r"\\\s*\n\s*", " ", workflow)
    invocations = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not re.match(r"^pytest\b", stripped):
            continue
        marker = re.search(r'-m\s+"([^"]+)"', stripped)
        paths = [
            token for token in re.findall(r"(?<!\S)(tests[^\s\\]*)", stripped)
        ]
        invocations.append((paths, marker.group(1) if marker else None))
    assert invocations, (
        "no `pytest` invocation found in %s — this guard reads the workflow to "
        "learn what CI actually runs, so it cannot work if the parse breaks"
        % WORKFLOW.name
    )
    return invocations


def _collect_nodeids(paths, marker=None):
    """The exact set of test nodeids one pytest invocation would run.

    `-o addopts=` is load-bearing. pytest.ini sets `addopts = -v`, and with -v
    the collector prints a <Module>/<Class> tree instead of nodeids, so a nodeid
    match finds nothing for EVERY invocation and any guard built on it reports
    universal coverage. Neutralise the ini options so the output shape is the
    one being parsed.
    """
    command = [sys.executable, "-m", "pytest", "-o", "addopts=",
               "--collect-only", "-q"]
    command += list(paths) if paths else ["tests"]
    if marker:
        command += ["-m", marker]
    result = subprocess.run(
        command, cwd=REPO, capture_output=True, text=True, timeout=900)
    return set(re.findall(r"^(\S+::\S+)\s*$", result.stdout, re.M))


def test_every_test_is_selected_by_some_ci_invocation():
    """No test may exist that no CI job runs. Checked per TEST, not per file.

    An earlier version of this guard compared whole FILES and was vacuous, which
    is how it was caught: removing the workflow step that runs the lane opt-outs
    left it green. `test_book_format_checksums_table_creation.py` holds ten
    fast-lane tests and one `integration`-marked one, so at file granularity the
    fast gate "covers" it while the only test that exercises the advertised
    behaviour is deselected and runs nowhere. Coverage is a property of a test,
    not of the file it happens to share.

    Everything is derived from the workflow's own invocations, so this stays
    true when CI changes instead of encoding today's answer.
    """
    everything = _collect_nodeids(["tests"])
    assert len(everything) > 5000, (
        "only %d tests collected from tests/ — the collector or the parse has "
        "broken, and this guard would pass vacuously" % len(everything))

    covered = set()
    for paths, marker in _workflow_pytest_invocations():
        covered |= _collect_nodeids(paths, marker)
    assert covered, "no CI invocation collected anything; the parse has drifted"

    uncovered = everything - covered
    by_file = {}
    for nodeid in sorted(uncovered):
        by_file.setdefault(nodeid.split("::", 1)[0], []).append(nodeid)
    assert not uncovered, (
        "%d test(s) in %d file(s) are run by NO workflow invocation:\n  %s\n\n"
        "A test that no job runs is a green gate over nothing. Either give it a "
        "lane the Fast Tests gate selects, or add a workflow step naming its "
        "path or marker." % (
            len(uncovered), len(by_file),
            "\n  ".join("%s (%d)" % (f, len(v)) for f, v in sorted(by_file.items())))
    )


def test_the_workflow_invocation_parser_sees_the_gate_it_is_built_on():
    """Vacuity guard for the test above.

    If the parser stopped finding invocations, or found only ones with no
    selector, every opt-out would trivially look covered. Pin that it recovers
    the Fast Tests gate specifically.
    """
    invocations = _workflow_pytest_invocations()
    markers = [marker for _paths, marker in invocations if marker]
    assert _fast_gate_marker_expression() in markers, (
        "the parser no longer recovers the Fast Tests `-m` expression from %s; "
        "test_every_lane_opt_out_is_actually_run_somewhere would pass "
        "vacuously" % WORKFLOW.name
    )
    assert len(invocations) >= 2, (
        "only %d pytest invocation(s) parsed from %s — the workflow runs more "
        "than that, so the parse has drifted" % (len(invocations), WORKFLOW.name)
    )
