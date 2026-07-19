"""Source-pin that every s6 service announces its entry on stdout.

Background — issue #868 (startup time) and PR #1002 (@chloeroform):

A user reported a ~5 minute unexplained gap in ``docker logs --timestamps``
between::

    [calibre-binaries-setup] Service completed successfully, exiting...
    <5 minutes of silence>
    [cwa-auto-library]: Existing library found at /calibre-library, mounting now...

The gap could not be attributed to a service, because the services that
run in that window emitted nothing when they *started* — only when they
finished, or from deeper inside a Python helper. With no entry marker
there is no way to tell "s6 had not started this unit yet" apart from
"this unit was running and working silently", which is exactly the
question a startup-time investigation has to answer first.

Two units were silent at entry when this test was written:

* ``cwa-auto-library`` — the ``DISABLE_LIBRARY_AUTOMOUNT`` branch logged,
  but the default path fell straight through to
  ``python3 .../auto_library.py`` with no output of its own. Fixed by
  @chloeroform in PR #1002.
* ``svc-calibre-web-automated`` — went directly from ``export`` to
  ``exec s6-notifyoncheck ...`` with no log line anywhere in the script.

The invariant this test pins: **the first executable top-level statement
of every s6 ``run`` script is an ``echo``**. Variable assignments,
``export``, ``set``, comments and shell control keywords are transparent;
the first statement that does real work or produces output must be the
service announcing itself. That keeps every second of container boot
attributable to a named service in a plain ``docker logs`` trace.

The check is deliberately top-level-only (indentation zero). An ``echo``
nested inside an ``if`` is conditional, so it cannot be relied on as an
entry marker — that is precisely how ``cwa-auto-library`` looked silent
on the default code path while still containing a tagged ``echo``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
S6_ROOT = REPO_ROOT / "root" / "etc" / "s6-overlay" / "s6-rc.d"

# Statements that carry no observable behaviour and may precede the
# announcement: shell options, environment setup, and control keywords
# whose bodies are indented (and therefore not top-level themselves).
_TRANSPARENT = re.compile(
    r"""^(
          set\b                 # set -e / set -euo pipefail
        | export\b              # export FOO=bar
        | local\b
        | readonly\b
        | declare\b
        | [A-Za-z_][A-Za-z0-9_]*=   # bare assignment: FOO=bar
        | (if|then|else|elif|fi|case|esac|in|while|until|do|done|function)\b
        | \}
        | \)
        | ;;
    )""",
    re.VERBOSE,
)


def _service_dirs() -> list[Path]:
    return sorted(
        d
        for d in S6_ROOT.iterdir()
        if d.is_dir() and (d / "run").is_file()
    )


def _first_executable_statement(run_script: Path) -> tuple[int, str] | None:
    """Return (lineno, text) of the first top-level statement that is not
    a comment, blank line, shebang, or a transparent no-op."""
    for lineno, raw in enumerate(run_script.read_text().splitlines(), 1):
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            continue  # shebang and comments
        if raw[:1] in (" ", "\t"):
            continue  # nested inside a block — not a top-level statement
        stripped = raw.strip()
        if _TRANSPARENT.match(stripped):
            continue
        return lineno, stripped
    return None


def test_s6_service_dirs_are_discovered():
    """Guard the guard: if the layout moves, fail loudly instead of
    silently passing over an empty set."""
    services = _service_dirs()
    assert len(services) >= 8, f"only found {len(services)} s6 run scripts under {S6_ROOT}"
    names = {d.name for d in services}
    for expected in ("cwa-auto-library", "svc-calibre-web-automated", "cwa-init"):
        assert expected in names, f"{expected} run script not found under {S6_ROOT}"


@pytest.mark.parametrize("service_dir", _service_dirs(), ids=lambda d: d.name)
def test_s6_run_script_announces_entry_before_doing_work(service_dir: Path):
    """The first thing an s6 service does must be to say that it started.

    Regression guard for the unattributable startup gap in #868.
    """
    run_script = service_dir / "run"
    first = _first_executable_statement(run_script)

    assert first is not None, (
        f"{service_dir.name}/run has no executable statement at all"
    )

    lineno, statement = first
    assert statement.startswith("echo"), (
        f"{service_dir.name}/run does not announce its entry: the first "
        f"top-level statement is line {lineno}: {statement!r}.\n"
        f"Add an unconditional entry line (e.g. "
        f'echo "[{service_dir.name}] Starting ...") before any work, so a '
        f"docker logs --timestamps trace can attribute boot time to this "
        f"service. See issue #868."
    )


@pytest.mark.parametrize("service_dir", _service_dirs(), ids=lambda d: d.name)
def test_s6_run_script_entry_line_identifies_the_service(service_dir: Path):
    """The entry line must name the service, so a reader of an interleaved
    log can tell which unit emitted it.

    Accepts either the dominant ``[<service-name>] ...`` convention or the
    older ``========== STARTING <NAME> ==========`` banner used by the two
    watcher services; both name the unit, and rewriting the banners would
    break users' existing log greps for no functional gain.
    """
    run_script = service_dir / "run"
    first = _first_executable_statement(run_script)
    assert first is not None
    lineno, statement = first
    if not statement.startswith("echo"):
        pytest.skip("covered by test_s6_run_script_announces_entry_before_doing_work")

    tag = f"[{service_dir.name}]"
    # ``cwa-auto-zipper`` -> ``CWA-AUTO-ZIPPER``; banner form is case-insensitive
    # and may abbreviate, so compare on the alphanumeric skeleton.
    banner_name = service_dir.name.replace("-", "").upper()
    skeleton = re.sub(r"[^A-Z0-9]", "", statement.upper())

    assert tag in statement or banner_name in skeleton, (
        f"{service_dir.name}/run line {lineno} announces entry but does not "
        f"identify the service: {statement!r}. Expected the '{tag}' prefix "
        f"(the convention used by most services) or a STARTING banner naming "
        f"the unit."
    )
