# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork #1277 — the classic admin Version Information
table reports the kepubify that is actually running.

Credit: @chloeroform (#1284) for retiring the ``/app/KEPUBIFY_RELEASE`` stamp.

This is the kepubify twin of #1274, and it inherits every trap that one
documented: the probe returns a banner *or* a translated ``LazyString``
diagnostic, it forks a subprocess on a request handler, and the path behind it
is a mutable admin setting. Those are pinned once, in
``test_1274_calibre_version_from_runtime.py``, against the shared helper both
labels now route through.

What is pinned *here* is what is specific to kepubify, and two of the three
were live defects in the first cut of #1284 that a fully green CI did not see:

* **The banner has no closing delimiter.** Calibre's is fenced —
  ``(calibre 9.11.0)`` — so a lazy ``(.+?)`` terminates on the ``)``. kepubify
  prints ``kepubify v4.0.4`` with nothing after the digits, so the same lazy
  quantifier stops at the first character it can: ``v4.0.4`` renders as
  ``vv4``. Every parametrised case below fails that way on the pre-fix build,
  including the ``-rc1`` suffix that a greedy-but-unanchored pattern truncates.

* **The version token already carries its own ``v``.** Calibre's does not, so
  the shared renderer prepends one. Capturing ``v...`` rather than the digits
  doubles it. The two banners disagree about this, which is exactly the kind of
  detail a copy-pasted regex gets wrong.

* **A NameError here takes the whole admin page down.** The lookup sits after
  the ``try``, deliberately — an undefined name is a bug in *this* module, not
  a failed probe, and must not be laundered into "Unknown". Nothing in the
  suite called the function, so the typo in the first cut reached a green
  ``Test Suite Summary`` (``__KEPUBIFYANNER_RE`` defined,
  ``_KEPUBIFY_BANNER_RE`` referenced). ``test_the_admin_row_is_rendered_from_a_live_probe``
  is the guard: it exercises the real call the admin page makes.
"""

import pathlib
import re
import subprocess

import pytest
from flask_babel import lazy_gettext as N_

import cps.admin as admin
import cps.converter as converter

pytestmark = pytest.mark.unit


# What `kepubify --version` actually prints. Confirmed against upstream source
# rather than inferred: cmd/kepubify/kepubify.go does
# ``fmt.Printf("kepubify %s\n", version)``, with ``version`` defaulting to
# "v4-dev" and set to the tag on a release build. So the token carries its own
# ``v`` — unlike calibre's, which does not.
#
# `_run_command_version` returns `match.string`, i.e. the whole matched line,
# not the capture.
_BANNER = "kepubify v4.0.4"

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    getattr(converter, "_version_probe_cache", {}).clear()
    yield
    getattr(converter, "_version_probe_cache", {}).clear()


# --- the label rendered into the admin table ---------------------------------


def test_banner_is_reduced_to_a_version_tag(monkeypatch):
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: _BANNER)
    assert admin.kepubify_version_label() == "v4.0.4"


@pytest.mark.parametrize(
    "banner,expected",
    [
        # The shipped shape. A lazy `(v.+?)` renders this as "vv4".
        ("kepubify v4.0.4", "v4.0.4"),
        ("kepubify v10.0", "v10.0"),
        # A build suffix must survive intact rather than be truncated.
        ("kepubify v4.0.4-rc1", "v4.0.4-rc1"),
        # Trailing build metadata must not be swallowed into the tag.
        ("kepubify v4.0.4 (go1.21)", "v4.0.4"),
        # An unreleased build. `version` defaults to "v4-dev" in
        # cmd/kepubify/kepubify.go, so this is a real shape, not a hypothetical
        # — and it is the one a greedy `(\d[\d.]*)` would silently truncate to
        # "v4", reporting a dev build as a release.
        ("kepubify v4-dev", "v4-dev"),
        # If a future release drops the `v`, the row still reads `vX.Y.Z`
        # rather than gaining or losing one.
        ("kepubify 4.0.4", "v4.0.4"),
    ],
)
def test_version_digits_are_taken_verbatim(monkeypatch, banner, expected):
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: banner)
    assert admin.kepubify_version_label() == expected


def test_the_v_prefix_is_never_doubled(monkeypatch):
    """The kepubify banner ships its own ``v``; calibre's does not. Capturing
    the ``v`` *and* prepending one is the copy-paste failure this catches."""
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: _BANNER)
    label = admin.kepubify_version_label()
    assert not label.startswith("vv"), label
    assert re.fullmatch(r"v\d+(\.\d+)*", label), label


def test_not_installed_diagnostic_survives_instead_of_becoming_unknown(monkeypatch):
    diagnostic = N_("not installed")
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: diagnostic)

    label = admin.kepubify_version_label()

    assert label is diagnostic
    assert str(label) != "Unknown"
    assert str(label) == "not installed"


def test_permissions_diagnostic_survives_instead_of_becoming_unknown(monkeypatch):
    diagnostic = N_("Execution permissions missing")
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: diagnostic)

    label = admin.kepubify_version_label()

    assert label is diagnostic
    assert str(label) == "Execution permissions missing"


def test_unparseable_output_is_passed_through_not_blanked(monkeypatch):
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: "some other tool 1.2")
    assert admin.kepubify_version_label() == "some other tool 1.2"


def test_an_unexpected_error_degrades_to_unknown_and_is_logged(monkeypatch, caplog):
    """"Unknown" survives only for a genuinely unexpected failure, and the
    reason has to reach the log rather than being swallowed."""
    def boom():
        raise RuntimeError("config not loaded")

    monkeypatch.setattr(converter, "get_kepubify_version", boom)

    with caplog.at_level("WARNING"):
        assert admin.kepubify_version_label() == "Unknown"

    assert any("config not loaded" in r.getMessage() for r in caplog.records), (
        "the swallowed exception must leave a diagnostic in the log"
    )


# --- the row the admin page actually renders ---------------------------------


def test_the_admin_row_is_rendered_from_a_live_probe(monkeypatch):
    """The guard that would have caught the NameError.

    Nothing in the suite called ``kepubify_version_label`` before this, so a
    name that does not resolve rendered a green CI and a 500 on
    ``/admin/view``. This exercises the exact call the template makes.
    """
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: _BANNER)
    monkeypatch.setattr(converter, "get_calibre_version", lambda: "ebook-convert (calibre 9.11.0)")

    versions = admin.cwa_get_package_versions()

    assert len(versions) == 3
    assert versions[1] == "v4.0.4", "the kepubify member is the live probe, not a stamp"
    assert versions[2] == "v9.11.0"


def test_the_kepubify_member_follows_the_binary_not_a_build_stamp(monkeypatch):
    """The bug #1277 reports: the row reported what the *build* pinned, so an
    image whose kepubify was replaced showed a version it was not running."""
    monkeypatch.setattr(converter, "get_calibre_version", lambda: "ebook-convert (calibre 9.11.0)")

    monkeypatch.setattr(converter, "get_kepubify_version", lambda: "kepubify v4.0.4")
    assert admin.cwa_get_package_versions()[1] == "v4.0.4"

    # Same install, binary swapped underneath — the row has to move with it.
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: "kepubify v4.1.0")
    assert admin.cwa_get_package_versions()[1] == "v4.1.0"


def test_the_diagnostic_reaches_the_admin_row_untranslated_into_unknown(monkeypatch):
    """An admin whose kepubify lacks the execute bit must keep the message
    that says so, in their language, rather than an opaque "Unknown"."""
    monkeypatch.setattr(converter, "get_calibre_version", lambda: "ebook-convert (calibre 9.11.0)")
    monkeypatch.setattr(converter, "get_kepubify_version", lambda: N_("Execution permissions missing"))

    kepubify_member = admin.cwa_get_package_versions()[1]

    assert not isinstance(kepubify_member, str)
    assert str(kepubify_member) == "Execution permissions missing"


def test_the_rendered_cell_shows_the_label_verbatim():
    """The unit tests above stop at the function; this renders the actual
    template fragment the admin page ships.

    Rendering, rather than grepping for ``{{kepubify_version}}``, is what
    catches the cell being fed the wrong context name or the value arriving
    HTML-escaped into something unreadable.
    """
    import jinja2

    html = (_REPO_ROOT / "cps" / "templates" / "admin.html").read_text(encoding="utf-8")
    rows = re.findall(r"<tr[^>]*>.*?</tr>", html, re.DOTALL)
    kepubify_rows = [r for r in rows if ">Kepubify<" in r]
    assert len(kepubify_rows) == 1, (
        f"expected exactly one Kepubify row in admin.html, found {len(kepubify_rows)}"
    )

    env = jinja2.Environment(autoescape=True)
    env.globals["_"] = lambda s: s
    rendered = env.from_string(kepubify_rows[0]).render(kepubify_version="v4.0.4")

    assert "v4.0.4" in rendered, rendered
    assert "vv4" not in rendered, rendered


def test_the_annotation_admits_the_translated_diagnostic():
    """Both probed members are legitimately LazyStrings; an annotation that
    says ``str`` invites the next reader to "fix" the passthrough."""
    import inspect

    annotation = str(inspect.signature(admin.cwa_get_package_versions).return_annotation)
    assert annotation.count("LazyString") >= 2, annotation


# --- one implementation, not two ---------------------------------------------


def test_both_version_labels_share_one_implementation():
    """@chloeroform flagged the duplication in #1284 and asked how to resolve
    it. Two copies of the same probe/diagnostic/render logic is the shape that
    lets one of them drift — the first cut already diverged on the try/except
    boundary. Pinned so a future edit re-forks them deliberately, not by
    accident.
    """
    import inspect

    calibre_src = inspect.getsource(admin.calibre_version_label)
    kepubify_src = inspect.getsource(admin.kepubify_version_label)

    for name, src in (("calibre", calibre_src), ("kepubify", kepubify_src)):
        assert "except" not in src, (
            f"{name}_version_label re-implements the probe/except dance instead "
            "of delegating to the shared renderer"
        )
        assert "isinstance" not in src, (
            f"{name}_version_label carries its own str/LazyString discriminator"
        )

    shared = inspect.getsource(admin._version_label)
    assert "isinstance" in shared, (
        "the str/LazyString discriminator is what stops the regex from raising; "
        "it has to live in the shared renderer both labels route through"
    )
    assert "except" in shared, (
        "the shared renderer owns the degrade-to-Unknown path"
    )


def test_the_banner_patterns_stay_distinct():
    """The two binaries print genuinely different banners. Sharing the renderer
    must not tempt anyone into sharing the pattern as well."""
    assert admin._CALIBRE_BANNER_RE.pattern != admin._KEPUBIFY_BANNER_RE.pattern

    assert admin._KEPUBIFY_BANNER_RE.search("kepubify v4.0.4")
    assert not admin._KEPUBIFY_BANNER_RE.search("ebook-convert (calibre 9.11.0)")
    assert admin._CALIBRE_BANNER_RE.search("ebook-convert (calibre 9.11.0)")
    assert not admin._CALIBRE_BANNER_RE.search("kepubify v4.0.4")


def test_admin_module_has_no_unresolved_module_level_names():
    """The typo class directly: a regex bound under one name and looked up
    under another. ``compile()`` accepts it; only the call fails, and the call
    was on a page no test loaded.
    """
    import inspect

    source = inspect.getsource(admin)
    defined = set(re.findall(r"^(_[A-Z0-9_]+)\s*=", source, re.MULTILINE))
    referenced = set(re.findall(r"\b(_[A-Z0-9_]+_RE)\b", source))

    unresolved = {
        name for name in referenced
        if name not in defined and not hasattr(admin, name)
    }
    assert unresolved == set(), (
        f"module-level names referenced but never bound: {sorted(unresolved)}"
    )

    orphans = {n for n in defined if n.endswith("_RE") and source.count(n) < 2}
    assert orphans == set(), (
        f"module-level patterns bound but never used — a rename left them behind: {sorted(orphans)}"
    )


# --- cross-file: the KEPUBIFY_RELEASE stamp file is gone entirely ------------


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def test_no_tracked_file_reads_or_writes_the_kepubify_stamp():
    """Mirrors the #1231/#1274 sweep for ``CALIBRE_RELEASE``. Matches the
    *path* form only — ``ARG KEPUBIFY_RELEASE`` and ``$KEPUBIFY_RELEASE`` are
    the build pin that selects which binary to download, and they stay.
    """
    offenders = []
    for rel in _tracked_files():
        if rel.startswith("tests/") or rel.endswith(".md"):
            continue
        path = _REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"/KEPUBIFY_RELEASE\b", text):
            offenders.append(rel)

    assert offenders == [], (
        "the /app/KEPUBIFY_RELEASE stamp file is retired in #1284; these files "
        f"still reference the path form: {offenders}"
    )


def test_the_stamp_sweep_is_not_vacuous():
    """``git ls-files`` returning nothing (wrong cwd, no git) would make the
    sweep above pass by looking at zero files."""
    tracked = _tracked_files()
    assert len(tracked) > 500, len(tracked)
    assert "Dockerfile" in tracked
    assert "cps/admin.py" in tracked


def test_the_build_arg_that_selects_the_binary_survives():
    """Retiring the runtime stamp must not disturb the pin that decides which
    kepubify the image downloads."""
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG KEPUBIFY_RELEASE=v?\d", dockerfile, re.MULTILINE), (
        "ARG KEPUBIFY_RELEASE is the SSOT for which kepubify the image downloads"
    )
    assert "kepubify-${KEPUBIFY_RELEASE}" in dockerfile, (
        "the mirror tag still has to resolve from the build ARG"
    )
