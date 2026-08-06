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
    assert "_read_log_tail" in body, (
        "/cover-enforcer-status must read via _read_log_tail(), which returns '' for a "
        "missing log instead of raising"
    )
    assert ".read()" not in body, (
        "the status route must not read the whole log — it is polled once a second and "
        "this app runs gevent without monkey.patch_all()"
    )


def _kill_watcher() -> ast.FunctionDef:
    # The watch loop lives in _watch_cover_enforcer(); kill_cover_enforcer() is now the
    # thin wrapper that guarantees the run claim is released on every exit path.
    return _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "_watch_cover_enforcer")


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


# ------------------------------------------------------ security fixes (review #1410)
#
# The cross-family review on #1410 found five HIGH issues in the first cut of this
# feature. These pin the fixes. Every one of them fails against that first cut, which is
# the bar the rest of this file's route tests do not meet — they search source text and
# stay green if the feature stops working.


def test_status_is_rendered_as_text_not_html():
    """Stored XSS: the run log is library content, and it was going through innerHTML.

    OBSERVED in review: `innerHTML = get.status.replace(/\\n/g, "<br>")`. The log is the
    enforcer's stdout, which prints book titles, authors and filenames. Anyone who can
    upload a book can put HTML in a title, so this executed in an ADMIN's session on this
    page — a privilege escalation from any upload-capable user.

    The newlines the `<br>` substitution existed for are handled by `white-space` now.
    """
    tpl = PAGE_TEMPLATE.read_text()

    # Strip comments/prose so the words "innerHTML" in the rationale don't fail this.
    code = re.sub(r"\{#.*?#\}", "", tpl, flags=re.S)
    code = re.sub(r"//.*", "", code)

    assert "innerHTML" not in code, (
        "the run log is assigned with innerHTML; it contains user-controllable library "
        "metadata and will execute as HTML in the admin's session"
    )
    assert re.search(r'innerStatus"\)\.textContent\s*=', code), (
        "the status must be written with textContent"
    )
    assert "white-space: pre-wrap" in tpl, (
        "textContent without pre-wrap collapses the log onto one line"
    )


@pytest.mark.parametrize("endpoint,rule", [
    ("start_cover_enforcer", "/cwa-cover-enforcer-start"),
    ("cancel_cover_enforcer", "/cover-enforcer-cancel"),
])
def test_state_changing_routes_are_post_only(endpoint, rule):
    """Start rewrites every book in the library; cancel kills a running job.

    @admin_required is authorization, not CSRF protection. As GETs these were reachable
    by making an admin follow a link. CSRFProtect only covers non-safe methods, so the
    method IS the fix — hence pinning it rather than pinning a decorator.
    """
    source = CWA_FUNCTIONS.read_text()
    decorator = re.search(
        rf"@cover_enforcer_ui\.route\(\s*'{re.escape(rule)}'\s*,\s*methods=(\[[^\]]*\])",
        source,
    )
    assert decorator, f"no methods= on the {rule} route"
    methods = decorator.group(1)
    assert '"GET"' not in methods and "'GET'" not in methods, (
        f"{rule} still accepts GET; it is state-changing and must be POST-only so CSRF "
        "protection applies"
    )
    assert '"POST"' in methods or "'POST'" in methods, f"{rule} must accept POST"


def test_page_invokes_the_actions_with_post():
    """A POST-only route is only reachable if the page stopped using <a href>."""
    tpl = PAGE_TEMPLATE.read_text()
    assert 'method: "POST"' in tpl, "the page never POSTs; the buttons cannot work"
    for endpoint in ("start_cover_enforcer", "cancel_cover_enforcer"):
        assert not re.search(rf"<a[^>]*url_for\('cover_enforcer_ui\.{endpoint}'\)", tpl), (
            f"{endpoint} is still a plain link, which issues a GET"
        )


def test_start_claims_the_run_before_touching_shared_state():
    """Two clicks used to wipe the live run's log and start a second watcher.

    cover_enforcer.py holds a cross-process lockfile, but it refuses AFTER this route has
    already truncated the shared log and spawned threads — so the second click destroyed
    the first run's output rather than being rejected. The claim has to happen here, and
    it has to happen before the truncate.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "start_cover_enforcer")
    src = ast.unparse(fn)

    assert "_cover_enforcer_lock" in src, "run admission is not guarded by a lock"
    assert "409" in src, "a concurrent start must be refused with 409, not run anyway"

    claim = src.index("_cover_enforcer_run['active'] = True")
    truncate = src.index("'w'")
    assert claim < truncate, (
        "the run is claimed after the log is truncated; a second start still erases the "
        "live run's log before being refused"
    )


def test_watcher_always_releases_the_run_claim():
    """A claim that outlives its run is a gate that disables its own repair.

    If the watcher can exit without clearing 'active', every later start returns 409
    until the container restarts — and the UI offers no way out of that state.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "kill_cover_enforcer")
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries, "kill_cover_enforcer() does not use try/finally"
    assert any(
        "_release_cover_enforcer_run" in ast.unparse(t.finalbody) for t in tries
    ), "the run claim is not released in a finally; a crashed watcher wedges all starts"


def test_spawn_failure_is_terminal_and_visible():
    """A failed Popen published nothing, so the watcher blocked forever on queue.get().

    The page also polls until it sees the end marker, so a spawn failure showed as a run
    that never progressed and never ended.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "cover_enforcer_start")
    src = ast.unparse(fn)
    assert "except Exception" in src, "a spawn failure is not caught"
    assert "Run Ended" in src, (
        "a failed spawn must still write the end marker or the page polls forever"
    )
    # ce_process stays None when the spawn fails, and publication happens in the finally,
    # so the sentinel and the success value are the same statement on every path.
    assert "queue.put(ce_process)" in src, (
        "the watcher must be unblocked with a sentinel when there is no process"
    )


def test_status_read_is_bounded():
    """Unbounded f.read() once a second, growing for the length of the run.

    This app runs gevent WITHOUT monkey.patch_all(), so a blocking read in a request
    handler stalls every request, not just this one — worst on the large libraries this
    feature exists for.
    """
    source = CWA_FUNCTIONS.read_text()
    fn = _find_function(ast.parse(source), "_read_log_tail")
    src = ast.unparse(fn)
    assert "SEEK_END" in src, "_read_log_tail() does not seek; it is not bounded"
    assert "FileNotFoundError" in src, "a missing log must return '' rather than raise"
    assert "COVER_ENFORCER_STATUS_TAIL_BYTES" in source, "no cap is defined"


def test_read_log_tail_returns_only_the_tail(tmp_path):
    """Behaviour, not shape: the whole point is that a big log costs a small read."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ce_tail", CWA_FUNCTIONS)
    # cwa_functions imports the Flask app; exercise the helper's logic directly instead
    # of importing the module, which is what the rest of this file avoids too.
    src = CWA_FUNCTIONS.read_text()
    fn_src = src[src.index("def _read_log_tail"):src.index("def is_cover_enforcer_finished")]
    ns = {"os": __import__("os"), "COVER_ENFORCER_STATUS_TAIL_BYTES": 64 * 1024}
    exec(compile(fn_src, "<tail>", "exec"), ns)
    read_tail = ns["_read_log_tail"]

    log = tmp_path / "run.log"
    log.write_text("A" * 5000 + "TAIL-MARKER")

    out = read_tail(str(log), limit=100)
    assert out.endswith("TAIL-MARKER")
    assert len(out) <= 100, "returned more than the requested limit"

    assert read_tail(str(tmp_path / "does-not-exist.log")) == "", (
        "a missing log must be '' so a first-ever page load does not 500"
    )

    # A cut landing mid-character must not raise.
    log.write_bytes("é".encode("utf-8") * 50)
    assert isinstance(read_tail(str(log), limit=5), str)


def test_cancel_does_not_pkill_by_script_path():
    """`pkill -f <script path>` killed CLI-started runs this UI never owned.

    The watcher already terminates the exact Popen it holds, with bounded waits, so the
    request only needs to signal.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "cancel_cover_enforcer")
    src = ast.unparse(fn)
    assert "pkill" not in src, (
        "cancel still uses pkill -f, which matches on the script path and kills runs "
        "started outside this UI"
    )
    assert "kill_cover_enforcer_trigger" in src, "cancel no longer signals the watcher"


def test_download_handler_lets_http_errors_through():
    """abort() raises HTTPException, which `except Exception` swallowed.

    403 and 404 both came back as 400 — collapsing exactly the distinction the checks
    above them exist to make.
    """
    # NB: cwa_functions.py defines THREE functions called download_current_log, one per
    # log-owning blueprint. _find_function returns the first, which is a different
    # blueprint's. Slice to this feature's route explicitly.
    source = CWA_FUNCTIONS.read_text()
    start = source.index(
        "@cover_enforcer_ui.route('/cwa-cover-enforcer/download-current-log")
    end = source.index("@cover_enforcer_ui.route('/cwa-cover-enforcer-start")
    fn = _find_function(ast.parse(source[start:end].lstrip()), "download_current_log")
    handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
    names = [ast.unparse(h.type) if h.type else "" for h in handlers]
    assert "HTTPException" in " ".join(names), (
        "HTTPException is not re-raised; abort(403)/abort(404) are rewritten to 400"
    )
    http_idx = next(i for i, n in enumerate(names) if "HTTPException" in n)
    broad_idx = next((i for i, n in enumerate(names) if n == "Exception"), None)
    if broad_idx is not None:
        assert http_idx < broad_idx, "HTTPException must be caught before Exception"


def test_poll_survives_a_failed_request():
    """One failed poll used to stop the page updating for the rest of the run.

    The catch logged to console and fell through to `get.status` on an undefined `get`,
    which threw before the setTimeout at the bottom — so polling silently never resumed
    and the user saw a frozen page with no error.
    """
    tpl = PAGE_TEMPLATE.read_text()
    # Body of the FIRST catch (the status poller's), from after its opening brace to the
    # matching close.
    opener = "} catch (e) {"
    body_start = tpl.index(opener) + len(opener)
    catch_block = tpl[body_start:tpl.index("}", body_start)]
    assert "setTimeout" in catch_block or "return" in catch_block, (
        "the catch neither reschedules nor returns; execution falls through to a "
        "dereference of the undefined response"
    )
    assert "res.ok" in tpl, "a non-2xx response is treated as a successful poll"
    assert "enforcerMessage" in tpl, "poll failures are not surfaced to the user"


def test_polling_chains_cannot_stack():
    """Pressing Start with a poll in flight must not leave two chains running.

    Introduced by the retry fix, not present in the original: the in-flight request
    resolves after the restart and schedules its own next tick, so two chains poll at
    once and only the last to assign `timeout` can ever be cleared. Every chain now
    carries the generation it started under and retires when superseded.
    """
    tpl = PAGE_TEMPLATE.read_text()
    assert "pollGeneration" in tpl, "no generation guard on the polling chain"
    assert tpl.count("generation !== pollGeneration") >= 2, (
        "both the success path and the retry path must retire a superseded chain"
    )
    # Every scheduled continuation must carry its generation forward, or the guard is
    # defeated by a bare re-entry that adopts the current generation.
    assert "setTimeout(getStatus," not in tpl, (
        "a continuation is scheduled without passing its generation"
    )
    assert "restartPolling" in tpl, "Start does not go through the generation bump"


# ------------------------------------------- second review round (fixes reviewed as code)
#
# The re-review looked at the fixes above as new code and found three ways to wedge the
# feature permanently. All three end the same way: the run claim is never released, so
# every later start returns 409 and the UI offers no route back. A gate that disables its
# own repair is worse than the bug it guards.


def test_read_log_tail_is_bounded_against_a_growing_file():
    """`f.read()` after a seek is bounded by the WRITER, not by `limit`.

    The child appends to this log continuously, so a bare read() keeps consuming whatever
    arrives after the seek — reintroducing the unbounded blocking read the helper exists
    to remove, precisely when the run is at its most productive.
    """
    source = CWA_FUNCTIONS.read_text()
    fn_src = source[source.index("def _read_log_tail"):source.index("def is_cover_enforcer_finished")]
    assert re.search(r"\.read\(limit\)", fn_src), (
        "_read_log_tail() must read(limit); a bare read() is bounded by the child's "
        "output rate, not by the cap"
    )
    assert not re.search(r"\.read\(\s*\)", fn_src), "unbounded read() still present"


def test_read_log_tail_never_exceeds_limit_while_the_file_grows(tmp_path):
    """Behavioural version of the above: grow the file, still get at most `limit`."""
    source = CWA_FUNCTIONS.read_text()
    fn_src = source[source.index("def _read_log_tail"):source.index("def is_cover_enforcer_finished")]
    ns = {"os": __import__("os"), "COVER_ENFORCER_STATUS_TAIL_BYTES": 64 * 1024}
    exec(compile(fn_src, "<tail>", "exec"), ns)
    read_tail = ns["_read_log_tail"]

    log = tmp_path / "run.log"
    log.write_bytes(b"X" * 10_000)
    assert len(read_tail(str(log), limit=1000)) <= 1000

    # Simulate the writer racing the reader: the file is much larger than the cap.
    log.write_bytes(b"Y" * 500_000)
    out = read_tail(str(log), limit=1000)
    assert len(out) <= 1000, f"returned {len(out)} bytes for a 1000-byte cap"


def test_spawn_thread_publishes_a_result_even_if_the_log_cannot_be_opened():
    """The log open() must be inside the try, or a disk/permission failure wedges all runs.

    Outside it, the spawn thread dies before anything reaches the queue. The watcher is
    not raising — it is looping waiting for a marker nobody will write — so its
    release-the-claim `finally` never runs and every later start returns 409 until the
    container restarts.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "cover_enforcer_start")

    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries, "cover_enforcer_start() has no try block"
    outer = tries[0]
    body_src = ast.unparse(outer.body)
    assert "cover-enforcer.log" in body_src and "open(" in body_src, (
        "the log open() is outside the try; a failure there publishes nothing and the "
        "watcher waits forever"
    )
    assert outer.finalbody, "no finally to guarantee publication"
    assert "queue.put" in ast.unparse(outer.finalbody), (
        "the queue result must be published from the finally, so EVERY path — including "
        "a failure to write the failure — still ends the run"
    )


def test_watcher_terminates_on_process_exit_not_only_on_the_marker():
    """A child that dies without printing the marker used to spin the watcher forever.

    An import error, a fatal signal or a disk-full write all exit without the end marker.
    Keying the loop's exit solely on a string in the log means the run claim is held for
    the life of the process.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "_watch_cover_enforcer")
    src = ast.unparse(fn)
    assert ".poll()" in src, (
        "the watcher never checks process liveness; a child that exits without the "
        "marker leaves it looping and the run claim held forever"
    )
    assert "returncode" in src, "the abnormal exit is not reported to the user"
    # And the abnormal path must still end the run for the PAGE, which stops polling
    # only on a marker.
    poll_idx = src.index(".poll()")
    tail = src[poll_idx:]
    assert "Run Ended" in tail, (
        "an abnormal exit must still write the end marker or the page polls forever"
    )


def test_watcher_does_not_double_consume_the_queue():
    """The liveness check drains the queue, so the cancel branch must not re-get it.

    A second blocking get() on an already-drained queue would block for its full timeout
    on every cancel, which is the hang the bounded get was added to prevent.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "_watch_cover_enforcer")
    src = ast.unparse(fn)
    assert "published" in src, "no guard tracking whether the queue was already drained"
    # The cancel branch must consult the already-held process before reaching for the queue.
    assert re.search(r"if published:\s*\n\s*ce_process = watched", src), (
        "cancel re-reads the queue instead of using the process the loop already holds"
    )


def test_publication_survives_a_failing_log_close():
    """`close()` flushes, so it is a write that can fail — on a full disk especially.

    Round three: the publication in the `finally` sat AFTER an unguarded `close()`, so a
    raise there skipped it and re-opened the wedge the finally was added to close. The
    guarantee the comment claims only holds if the close cannot pre-empt the put.
    """
    fn = _find_function(ast.parse(CWA_FUNCTIONS.read_text()), "cover_enforcer_start")
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    outer = tries[0]
    final_src = ast.unparse(outer.finalbody)

    assert "queue.put" in final_src, "publication is not in the finally"
    # The close must be individually guarded, not merely present.
    inner_tries = [n for n in ast.walk(ast.Module(body=outer.finalbody, type_ignores=[]))
                   if isinstance(n, ast.Try)]
    assert inner_tries, "close() in the finally is unguarded; a raise there skips the put"
    assert any("close()" in ast.unparse(t.body) for t in inner_tries), (
        "the guarded block in the finally is not the close()"
    )
