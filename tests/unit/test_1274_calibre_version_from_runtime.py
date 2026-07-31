# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork #1274 — the classic admin Version Information
table reports the calibre that is actually running.

Credit: @chloeroform (#1274) for retiring the ``/app/CALIBRE_RELEASE`` stamp.

The stamp recorded what the *build* pinned, so an image whose calibre was
replaced, or an install pointing ``config_converterpath`` somewhere else,
reported a version it was not running. The row now derives from
``converter.get_calibre_version()`` — the same helper the SPA ``/stats`` page
already used.

Three things about that swap are easy to get wrong, and all three are pinned
below because none of them fails loudly:

* **The diagnostics are LazyStrings, not str.** ``get_calibre_version()``
  returns the ``ebook-convert (calibre 9.11.0)`` banner *or* a translated
  ``not installed`` / ``Execution permissions missing``. Running a regex over
  the latter raises ``TypeError``, and a bare ``except`` turns an actionable,
  translated diagnostic into an opaque, untranslated "Unknown".

* **The probe forks a subprocess and blocks on wait().** cps runs gevent
  without ``monkey.patch_all()``, so an uncached probe on a request handler
  stalls every other greenlet — the exact failure mode fixed in #1270. The
  admin table lands here on an ordinary page load, so the result is memoised.

* **The memo key is a mutable admin setting.** ``config_converterpath`` is
  editable in the UI. Caching on the function (rather than on the path) freezes
  the answer for the life of the process, so an admin who corrects a wrong path
  keeps seeing the stale value until the container restarts. Failures must not
  be cached at all, or installing calibre appears to have no effect.
"""

import pytest
from flask_babel import lazy_gettext as N_

import cps.admin as admin
import cps.converter as converter

pytestmark = pytest.mark.unit


_BANNER = "ebook-convert (calibre 9.11.0)"


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    # getattr-with-default so these tests still *run* (and fail on behaviour)
    # against a build that has no probe cache at all, rather than erroring out
    # at setup and hiding which defect they catch.
    getattr(converter, "_version_probe_cache", {}).clear()
    yield
    getattr(converter, "_version_probe_cache", {}).clear()


# --- the label rendered into the admin table ---------------------------------


def test_banner_is_reduced_to_a_version_tag(monkeypatch):
    monkeypatch.setattr(converter, "get_calibre_version", lambda: _BANNER)
    assert admin.calibre_version_label() == "v9.11.0"


@pytest.mark.parametrize(
    "banner,expected",
    [
        ("ebook-convert (calibre 9.11.0)", "v9.11.0"),
        ("ebook-convert (calibre 10.0)", "v10.0"),
        # A build suffix must survive intact rather than be truncated.
        ("ebook-convert (calibre 7.16.0-rc1)", "v7.16.0-rc1"),
    ],
)
def test_version_digits_are_taken_verbatim(monkeypatch, banner, expected):
    monkeypatch.setattr(converter, "get_calibre_version", lambda: banner)
    assert admin.calibre_version_label() == expected


def test_not_installed_diagnostic_survives_instead_of_becoming_unknown(monkeypatch):
    """The regression this guards: a LazyString through a regex raises
    TypeError, and ``except Exception: "Unknown"`` swallows the reason."""
    diagnostic = N_("not installed")
    monkeypatch.setattr(converter, "get_calibre_version", lambda: diagnostic)

    label = admin.calibre_version_label()

    assert label is diagnostic
    assert str(label) != "Unknown"
    assert str(label) == "not installed"


def test_permissions_diagnostic_survives_instead_of_becoming_unknown(monkeypatch):
    diagnostic = N_("Execution permissions missing")
    monkeypatch.setattr(converter, "get_calibre_version", lambda: diagnostic)

    label = admin.calibre_version_label()

    assert label is diagnostic
    assert str(label) == "Execution permissions missing"


def test_unparseable_output_is_passed_through_not_blanked(monkeypatch):
    """An unexpected banner shape is still more useful to an admin than
    "Unknown" — it is the actual output of the binary they configured."""
    monkeypatch.setattr(converter, "get_calibre_version", lambda: "some other tool 1.2")
    assert admin.calibre_version_label() == "some other tool 1.2"


def test_label_never_raises_on_the_two_real_return_shapes(monkeypatch):
    for value in (_BANNER, N_("not installed")):
        monkeypatch.setattr(converter, "get_calibre_version", lambda v=value: v)
        admin.calibre_version_label()  # must not raise


# --- the probe cache ---------------------------------------------------------


def _count_probes(monkeypatch, results):
    """Patch the subprocess-backed probe; return a list recording each call."""
    calls = []

    def fake(path, pattern, argument=None):
        calls.append(path)
        return results(path) if callable(results) else results

    monkeypatch.setattr(converter, "_run_command_version", fake)
    return calls


def test_successful_probe_runs_once_per_path(monkeypatch):
    """The subprocess must not be forked on every admin page render."""
    monkeypatch.setattr(converter.config, "config_converterpath", "/usr/bin/ebook-convert", raising=False)
    calls = _count_probes(monkeypatch, _BANNER)

    assert converter.get_calibre_version() == _BANNER
    assert converter.get_calibre_version() == _BANNER
    assert converter.get_calibre_version() == _BANNER

    assert calls == ["/usr/bin/ebook-convert"], "probe should have run exactly once"


def test_changing_the_converter_path_re_probes(monkeypatch):
    """config_converterpath is admin-editable; a corrected path must take
    effect without a container restart."""
    calls = _count_probes(
        monkeypatch,
        lambda path: f"ebook-convert (calibre {'9.11.0' if 'good' in path else '1.0.0'})",
    )

    monkeypatch.setattr(converter.config, "config_converterpath", "/bad/ebook-convert", raising=False)
    assert converter.get_calibre_version() == "ebook-convert (calibre 1.0.0)"

    monkeypatch.setattr(converter.config, "config_converterpath", "/good/ebook-convert", raising=False)
    assert converter.get_calibre_version() == "ebook-convert (calibre 9.11.0)"

    assert calls == ["/bad/ebook-convert", "/good/ebook-convert"]


def test_failures_are_never_cached(monkeypatch):
    """Installing calibre, or fixing its execute bit, must be picked up on the
    next page load — not cached as 'not installed' for the process lifetime."""
    monkeypatch.setattr(converter.config, "config_converterpath", "/usr/bin/ebook-convert", raising=False)
    state = {"value": N_("not installed")}
    calls = _count_probes(monkeypatch, lambda _path: state["value"])

    first = converter.get_calibre_version()
    assert str(first) == "not installed"

    state["value"] = _BANNER
    assert converter.get_calibre_version() == _BANNER
    assert len(calls) == 2, "a failed probe must be retried, not memoised"

    # ...and once it succeeds it is memoised like any other success.
    converter.get_calibre_version()
    assert len(calls) == 2


def test_upgrading_calibre_in_place_re_probes(monkeypatch, tmp_path):
    """The stale case the path key alone does not cover.

    Upgrading calibre leaves ``config_converterpath`` identical, so a memo keyed
    only on the path keeps serving the old banner until the process restarts —
    which is the very "reports a version it is not running" bug this change
    exists to fix.
    """
    binary = tmp_path / "ebook-convert"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(converter.config, "config_converterpath", str(binary), raising=False)

    state = {"value": "ebook-convert (calibre 9.11.0)"}
    calls = _count_probes(monkeypatch, lambda _p: state["value"])

    assert converter.get_calibre_version() == "ebook-convert (calibre 9.11.0)"
    assert converter.get_calibre_version() == "ebook-convert (calibre 9.11.0)"
    assert len(calls) == 1

    # Same path, different file underneath.
    binary.write_text("#!/bin/sh\n# upgraded\n")
    state["value"] = "ebook-convert (calibre 10.2.0)"

    assert converter.get_calibre_version() == "ebook-convert (calibre 10.2.0)", (
        "replacing the binary at the configured path must invalidate the memo"
    )
    assert len(calls) == 2


def test_a_missing_binary_is_not_memoised_as_present(monkeypatch, tmp_path):
    """Deleting the binary must not leave the last good banner on the page."""
    binary = tmp_path / "ebook-convert"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(converter.config, "config_converterpath", str(binary), raising=False)

    state = {"value": "ebook-convert (calibre 9.11.0)"}
    _count_probes(monkeypatch, lambda _p: state["value"])
    assert converter.get_calibre_version() == "ebook-convert (calibre 9.11.0)"

    binary.unlink()
    state["value"] = N_("not installed")
    assert str(converter.get_calibre_version()) == "not installed"


def test_the_probe_never_runs_on_the_request_greenlet(monkeypatch):
    """cps runs gevent without monkey.patch_all(), so the blocking fork has to
    go through the bounded offload pool #1270 introduced. Memoising is not
    enough on its own — the *first* probe still lands on a real page load."""
    monkeypatch.setattr(converter.config, "config_converterpath", "/usr/bin/ebook-convert", raising=False)
    _count_probes(monkeypatch, _BANNER)

    offloaded = []
    real = converter.parallel.run_blocking
    monkeypatch.setattr(
        converter.parallel, "run_blocking",
        lambda fn: (offloaded.append(fn), real(fn))[1],
    )

    assert converter.get_calibre_version() == _BANNER
    assert len(offloaded) == 1, "the subprocess probe must be offloaded, not run inline"


def test_every_version_probe_is_protected_not_just_calibre(monkeypatch, tmp_path):
    """``/stats`` forks three probes, not one.

    Protecting only the calibre probe would leave unrar and kepubify still
    forking uncached on the request greenlet from the same page — half the fix.
    The memo and the offload therefore live in the shared helper.
    """
    binary = tmp_path / "tool"
    binary.write_text("#!/bin/sh\n")
    for setting in ("config_converterpath", "config_rarfile_location", "config_kepubifypath"):
        monkeypatch.setattr(converter.config, setting, str(binary), raising=False)

    banners = {
        r'ebook-convert.*\(calibre': "ebook-convert (calibre 9.11.0)",
        r'UNRAR.*\d': "UNRAR 7.01 freeware",
        r'kepubify\s': "kepubify 4.0.4",
    }
    calls = []

    def fake(path, pattern, argument=None):
        calls.append(pattern)
        return banners.get(pattern, N_("not installed"))

    monkeypatch.setattr(converter, "_run_command_version", fake)

    for _ in range(3):
        # Each must keep returning ITS OWN banner. All three settings point at
        # the same file here, so a memo keyed on the path alone would serve
        # whichever probe ran first to the other two.
        assert converter.get_calibre_version() == "ebook-convert (calibre 9.11.0)"
        assert converter.get_unrar_version() == "UNRAR 7.01 freeware"
        assert converter.get_kepubify_version() == "kepubify 4.0.4"

    assert sorted(calls) == sorted(banners), (
        f"each probe should have run exactly once across three page loads, got {calls}"
    )


def test_the_two_unrar_probes_are_cached_separately(monkeypatch, tmp_path):
    """get_unrar_version probes the same path twice with different patterns on
    a miss, and only the second one succeeds. Keying the memo on the path alone
    would cache the fallback's answer under the first probe's key, so the next
    call would short-circuit on it and never exercise the fallback again."""
    binary = tmp_path / "unrar"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(converter.config, "config_rarfile_location", str(binary), raising=False)

    probes = []

    def fake(path, pattern, argument=None):
        probes.append((pattern, argument))
        return "unrar 7.01" if argument == "-V" else N_("not installed")

    monkeypatch.setattr(converter, "_run_command_version", fake)

    assert converter.get_unrar_version() == "unrar 7.01"
    assert converter.get_unrar_version() == "unrar 7.01"

    # Three probes, not four: the failing first probe re-runs on the second
    # call (failures are never cached), then the fallback answers from its own
    # cache entry. A path-only key would have collapsed those two entries.
    assert probes == [
        (r'UNRAR.*\d', None), (r'unrar.*\d', '-V'),
        (r'UNRAR.*\d', None),
    ], probes


def test_a_none_converter_path_does_not_crash_or_poison_the_cache(monkeypatch):
    """config_converterpath is nullable in the settings table."""
    monkeypatch.setattr(converter.config, "config_converterpath", None, raising=False)
    calls = _count_probes(monkeypatch, N_("not installed"))

    assert str(converter.get_calibre_version()) == "not installed"
    assert calls == [""]
    assert converter._version_probe_cache == {}


def test_the_admin_table_is_not_frozen_for_the_life_of_the_process(monkeypatch):
    """``@cache`` on ``cwa_get_package_versions`` would memoise the *whole* row.

    Two of its three members are genuinely build-time, but the calibre version
    is not: it follows ``config_converterpath``. Caching at this level means an
    admin who corrects a wrong converter path keeps seeing the old value until
    the container restarts, with nothing in the UI to suggest why.
    """
    state = {"value": "ebook-convert (calibre 1.0.0)"}
    monkeypatch.setattr(converter, "get_calibre_version", lambda: state["value"])

    assert admin.cwa_get_package_versions()[2] == "v1.0.0"

    state["value"] = "ebook-convert (calibre 9.11.0)"
    assert admin.cwa_get_package_versions()[2] == "v9.11.0", (
        "the version row must re-read, not serve a process-lifetime cache"
    )


# --- source pins for the two silent-failure shapes ---------------------------


def test_an_unexpected_error_degrades_to_unknown_and_is_logged(monkeypatch, caplog):
    """"Unknown" is still the right answer for a genuinely unexpected failure —
    the version row must not take the admin page down. What changed is that it
    is no longer reachable from the two *documented* return shapes, and that
    the reason is logged instead of silently swallowed."""

    def boom():
        raise RuntimeError("config not loaded")

    monkeypatch.setattr(converter, "get_calibre_version", boom)

    with caplog.at_level("WARNING"):
        assert admin.calibre_version_label() == "Unknown"

    assert any("config not loaded" in r.getMessage() for r in caplog.records), (
        "the swallowed exception must leave a diagnostic in the log"
    )


def test_the_str_lazystring_discriminator_is_what_guards_the_regex():
    """Pinned at source because the failure is silent: without it the regex
    raises TypeError on every diagnostic and the except turns it into
    "Unknown" — green tests, wrong page."""
    import inspect

    src = inspect.getsource(admin.calibre_version_label)
    assert "isinstance" in src, (
        "the str/LazyString discriminator is what stops the regex from raising"
    )


def test_admin_module_compiles_without_syntax_warnings():
    """``'.*calibre (.*)\\)'`` without an r-prefix is an invalid escape
    sequence — a SyntaxWarning on 3.12+ and a SyntaxError in a later Python.
    """
    import pathlib
    import warnings

    source = pathlib.Path(admin.__file__).read_text(encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile(source, admin.__file__, "exec")
    offenders = [w for w in caught if issubclass(w.category, SyntaxWarning)]
    assert offenders == [], [str(w.message) for w in offenders]


def test_package_versions_returns_exactly_what_its_caller_unpacks():
    """The annotation said 4-wide while the function returned 3 — the kind of
    drift that makes an unpack at the call site look safe when it is not.

    Asserted against the runtime contract and the real call site, not against
    the spelling of the annotation: the third member is legitimately a
    LazyString when calibre could not be probed, so counting ``str`` in the
    annotation would pin the wrong thing.
    """
    import inspect

    versions = admin.cwa_get_package_versions()
    assert len(versions) == 3

    src = inspect.getsource(admin.admin)
    assert "cwa_version, kepubify_version, calibre_version = cwa_get_package_versions()" in src, (
        "the caller's unpack width must match what the function returns"
    )


def test_the_annotation_admits_the_translated_diagnostic(monkeypatch):
    """The declared type has to allow the LazyString the function deliberately
    returns, or the next reader 'fixes' the passthrough to satisfy it."""
    import inspect

    annotation = str(inspect.signature(admin.cwa_get_package_versions).return_annotation)
    assert "LazyString" in annotation, annotation

    monkeypatch.setattr(converter, "get_calibre_version", lambda: N_("not installed"))
    assert not isinstance(admin.cwa_get_package_versions()[2], str)
