# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression pins for the library-wide enforcement run in the web UI (fork #1408).

Reported by @stripeymonkey on #1372: the v4.1.30 notes pointed at "one pass from
Settings via the cover and metadata enforcement", and no such control existed.
CWA Settings only has the ``auto_metadata_enforcement`` toggle, which is the
*on-edit* service. The only library-wide pass was ``cover_enforcer.py -all``,
reachable exclusively over ``docker exec``.

Two halves have to hold for the page to work, and each fails silently in a
different way, so both are pinned here.

**Script half.** ``enforce_all_covers()`` printed a total up front and then ran
the loop silently. The status poller derives the progress bar from the LAST
``n/n`` match in the log (``extract_progress``), so without a per-book line the
bar reads 0/0 for the entire run and then jumps to done — on a large library
that is indistinguishable from a hung job. It also printed no end marker, and
``is_cover_enforcer_finished()`` keys the kill thread's exit on exactly that
string, so the watcher thread would spin at 20Hz forever after a completed run.

The end marker is pinned as printed *unconditionally* after the summary
if/elif chain rather than inside a branch. ``enforce_all_covers`` returns
``(False, False, False)`` when the library holds no supported files, which takes
the first branch — marker inside the chain would mean an empty library hangs the
page and leaks the watcher thread.

**Flask half.** The routes and the blueprint registration are pinned
structurally. The routes actually serving is proven against a live container in
the PR, not here; what this file catches is the silent-drop case where the
blueprint is defined but never registered in ``main.py``, which yields a 404 on
a page whose nav button renders fine.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
COVER_ENFORCER = REPO_ROOT / "scripts" / "cover_enforcer.py"
CWA_FUNCTIONS = REPO_ROOT / "cps" / "cwa_functions.py"
MAIN_PY = REPO_ROOT / "cps" / "main.py"
ADMIN_HTML = REPO_ROOT / "cps" / "templates" / "admin.html"
PAGE_TEMPLATE = REPO_ROOT / "cps" / "templates" / "cwa_cover_enforcer.html"

RUN_ENDED_MARKER = "NextGen Cover & Metadata Enforcement Service - Run Ended: "


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


def _enforce_all_covers() -> ast.FunctionDef:
    return _find_function(ast.parse(COVER_ENFORCER.read_text()), "enforce_all_covers")


# ---------------------------------------------------------------- script half


def test_enforce_all_covers_prints_per_book_progress():
    """The book loop must emit a "n/n" line, or the progress bar never moves.

    extract_progress() takes the LAST (\\d+)/(\\d+) in the log. Pinning that the
    loop is enumerate-driven and prints inside the body, so the denominator is
    the real book count rather than a constant.
    """
    fn = _enforce_all_covers()

    for_loops = [n for n in ast.walk(fn) if isinstance(n, ast.For)]
    assert for_loops, "enforce_all_covers() has no loop over books"

    book_loop = None
    for loop in for_loops:
        if isinstance(loop.iter, ast.Call) and getattr(loop.iter.func, "id", "") == "enumerate":
            book_loop = loop
            break
    assert book_loop is not None, (
        "the book loop must be enumerate()-driven so each book can report its "
        "index; without it there is no per-book n/n for extract_progress()"
    )

    printed = [
        n for n in ast.walk(book_loop)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"
    ]
    assert printed, "no print() inside the book loop — the progress bar cannot move"

    progress_sources = [ast.unparse(n) for n in printed]
    assert any("/" in src and "index" in src.lower() for src in progress_sources), (
        "expected a progress print of the form '... {index}/{total} ...' inside "
        f"the book loop, found: {progress_sources}"
    )


def test_progress_line_is_parseable_by_extract_progress():
    """The emitted shape must satisfy the regex the status route actually uses.

    Pinning the format against extract_progress()'s own regex rather than a
    hand-copied one, so a change to either side goes red.
    """
    extract_src = CWA_FUNCTIONS.read_text()
    pattern_match = re.search(r"re\.findall\(r'(.+?)',\s*log_content\)", extract_src)
    assert pattern_match, "extract_progress() no longer uses a findall regex"
    progress_regex = pattern_match.group(1)

    sample = "[cover-metadata-enforcer]: Enforcing book 3/57 ..."
    found = re.findall(progress_regex, sample)
    assert found == [("3", "57")], (
        f"the progress line format is not parseable by extract_progress's "
        f"regex {progress_regex!r}; got {found}"
    )


def test_run_ended_marker_is_printed_unconditionally_for_all_flag():
    """An empty library must still terminate the watcher thread.

    enforce_all_covers() returns (False, False, False) with no supported files,
    which lands in the first summary branch. If the marker were printed inside
    the if/elif chain, is_cover_enforcer_finished() would never return True and
    kill_cover_enforcer() would spin forever.
    """
    source = COVER_ENFORCER.read_text()
    assert RUN_ENDED_MARKER in source, (
        "cover_enforcer.py prints no run-ended marker; the web UI's kill thread "
        "keys its exit on this exact string"
    )

    tree = ast.parse(source)
    main_fn = _find_function(tree, "main")

    marker_stmt = None
    holder = None
    for node in ast.walk(main_fn):
        for field in ("body", "orelse"):
            for stmt in getattr(node, field, []) or []:
                if RUN_ENDED_MARKER in ast.unparse(stmt):
                    marker_stmt, holder = stmt, node
    assert marker_stmt is not None, "run-ended marker is not printed from main()"

    # The marker must be a sibling of the summary if/elif chain, not a branch of it.
    assert not isinstance(holder, ast.If) or RUN_ENDED_MARKER not in ast.unparse(holder.test), (
        "run-ended marker must not be gated on a summary condition"
    )
    enclosing = ast.unparse(holder)
    assert "n_enforced" not in ast.unparse(marker_stmt), (
        "the marker line must not depend on the enforcement result"
    )
    assert "args.all" in enclosing or "enforce_all_covers" in enclosing, (
        "the marker should be emitted from the -all dispatch branch"
    )


# ----------------------------------------------------------------- flask half


@pytest.mark.parametrize(
    "rule,endpoint",
    [
        ("/cwa-cover-enforcer-overview", "show_cover_enforcer_page"),
        ("/cwa-cover-enforcer-start", "start_cover_enforcer"),
        ("/cover-enforcer-cancel", "cancel_cover_enforcer"),
        ("/cover-enforcer-status", "get_status"),
        ("/cwa-cover-enforcer/log-archive", "show_cover_enforcer_logs"),
    ],
)
def test_enforcement_routes_declared(rule: str, endpoint: str):
    """Each route exists on the cover_enforcer_ui blueprint."""
    source = CWA_FUNCTIONS.read_text()
    assert "cover_enforcer_ui = Blueprint(" in source, "blueprint is not defined"
    pattern = rf"@cover_enforcer_ui\.route\(\s*['\"]{re.escape(rule)}['\"]"
    assert re.search(pattern, source), f"no cover_enforcer_ui route for {rule}"
    assert re.search(rf"def {endpoint}\(", source), f"handler {endpoint}() missing"


def test_enforcement_routes_are_admin_gated():
    """A library-wide rewrite of every ebook file must not be reachable by a
    normal user. Every handler in the block carries @admin_required."""
    source = CWA_FUNCTIONS.read_text()
    block = source[source.index("cover_enforcer_start"):]
    routes = re.findall(
        r"@cover_enforcer_ui\.route\([^)]*\)\s*(.*?)\s*def \w+\(",
        block,
        flags=re.DOTALL,
    )
    assert routes, "no cover_enforcer_ui routes found to check"
    for decorators in routes:
        assert "@admin_required" in decorators, (
            f"an enforcement route is not admin-gated: {decorators!r}"
        )


def test_status_route_tolerates_missing_log():
    """First-ever page load has no log file; the poller must not 500.

    The epub fixer's equivalent open()s blindly. Pinning that this one checks
    existence first, since the page polls the endpoint on an interval and a 500
    there shows as a permanently broken page rather than an empty one.
    """
    source = CWA_FUNCTIONS.read_text()
    start = source.index("@cover_enforcer_ui.route('/cover-enforcer-status'")
    body = source[start:start + 900]
    assert "os.path.exists" in body, (
        "/cover-enforcer-status must tolerate a missing log file"
    )


def _kill_watcher() -> ast.FunctionDef:
    return _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "kill_cover_enforcer")


def test_run_log_is_opened_in_append_mode():
    """The child's stdout handle must be O_APPEND, not 'w'.

    OBSERVED failure: with 'w', the subprocess and the kill thread each hold their
    own file offset. On cancel the kill thread appends the TERMINATED marker, then
    the dying child's traceback writes at its own lower offset and overwrites it.
    The page stops polling only when it sees that marker, so the run looks live
    forever. O_APPEND forces both writers to the current end of file.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "cover_enforcer_start")
    opens = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"
    ]
    assert opens, "cover_enforcer_start no longer opens the run log"
    modes = [ast.unparse(c.args[1]) for c in opens if len(c.args) > 1]
    assert modes, "the run log is opened without an explicit mode"
    assert all("'w'" not in m for m in modes), (
        f"run log opened in truncating mode {modes}; the cancellation marker will be "
        "clobbered by the child's final writes"
    )
    assert any("'a'" in m for m in modes), f"expected append mode, got {modes}"


def test_cancel_reaps_the_child_before_writing_the_marker():
    """terminate() alone leaves a zombie and a child that can still write.

    OBSERVED: cancelling left a defunct python3 under the Flask process, and the
    marker never reached the log. Both are fixed by waiting for the child before
    writing. Pinning the ORDER, since a wait() placed after the marker write would
    reproduce the original bug.
    """
    fn = _kill_watcher()
    src = ast.unparse(fn)

    assert ".terminate()" in src, "cancel no longer signals the child"
    assert ".wait(" in src, (
        "cancel does not wait() for the child — it is left defunct and can still "
        "write over the cancellation marker"
    )
    assert ".kill()" in src, (
        "no SIGKILL escalation; enforcement blocks in calibredb/ebook-polish and can "
        "outlive a SIGTERM, which would hang the watcher thread"
    )

    marker_pos = src.index("TERMINATED BY USER AT")
    wait_pos = src.index(".wait(")
    assert wait_pos < marker_pos, (
        "the child must be reaped BEFORE the cancellation marker is written, or its "
        "final output lands on top of the marker"
    )


def test_cancel_clears_the_lock_only_after_the_child_exits():
    """cover_enforcer.py owns that lock via atexit.register(removeLock).

    OBSERVED: deleting it while the script was still alive made the script's own
    atexit handler raise FileNotFoundError straight into the user-visible run log.
    Keeping the removal as a post-wait safety net (atexit does not run on SIGKILL)
    is correct; doing it before the wait is not.
    """
    src = ast.unparse(_kill_watcher())
    assert "cover_enforcer.lock" in src, "the SIGKILL lock safety net was dropped"
    assert src.index(".wait(") < src.index("cover_enforcer.lock"), (
        "the lock is cleared before the child exits; cover_enforcer.py's atexit "
        "removeLock() will then raise into the run log"
    )
    enforcer_src = COVER_ENFORCER.read_text()
    assert "atexit.register(removeLock)" in enforcer_src, (
        "cover_enforcer.py no longer self-manages its lock — the ordering rationale "
        "above needs re-checking"
    )


def test_blueprint_is_registered_in_main():
    """Defined-but-unregistered is the silent-404 failure mode."""
    main_src = MAIN_PY.read_text()
    assert "cover_enforcer_ui" in main_src, (
        "cover_enforcer_ui is never imported in cps/main.py — every route 404s "
        "while the admin nav button still renders"
    )
    assert re.search(r"register_blueprint\(\s*cover_enforcer_ui\s*\)", main_src), (
        "cover_enforcer_ui imported but never registered"
    )


def test_admin_page_links_to_the_run():
    """The control has to be discoverable — that gap is what #1408 is about."""
    admin_src = ADMIN_HTML.read_text()
    assert "cover_enforcer_ui.show_cover_enforcer_page" in admin_src, (
        "admin page has no link to the enforcement run"
    )


def test_page_template_exists_and_polls_status():
    """The page must poll its OWN status endpoint.

    Pinned against the ``url_for`` endpoint name rather than the URL string:
    the template resolves the route through Flask, which is what keeps it
    working under a reverse-proxy subpath. The failure this guards is a
    copy-paste of the epub fixer page still pointing at
    ``epub_fixer.get_status`` — the page would render and poll happily while
    showing a different service's log.
    """
    assert PAGE_TEMPLATE.exists(), "cwa_cover_enforcer.html is missing"
    tpl = PAGE_TEMPLATE.read_text()
    assert "cover_enforcer_ui.get_status" in tpl, (
        "page never polls its own status endpoint"
    )
    for foreign in ("epub_fixer.", "convert_library."):
        assert foreign not in tpl, (
            f"template still references {foreign} — leftover from the copied page"
        )


def test_page_reacts_to_both_terminal_markers():
    """The poller stops on completion AND on user cancellation.

    Both strings are produced elsewhere (the script prints one, the kill thread
    appends the other), so a change on either side that misses the template
    leaves the page spinning after the run is over.
    """
    tpl = PAGE_TEMPLATE.read_text()
    assert RUN_ENDED_MARKER.rstrip() in tpl, (
        "page does not detect the run-ended marker; it polls forever after a "
        "completed run"
    )
    cancel_marker = "TERMINATED BY USER AT"
    assert cancel_marker in tpl, "page does not detect a user cancellation"
    assert cancel_marker in CWA_FUNCTIONS.read_text(), (
        "kill thread no longer writes the cancellation marker the page waits for"
    )
