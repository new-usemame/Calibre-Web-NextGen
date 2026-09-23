# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The three admin service pages poll their status endpoint through one log reader.

Reported by @TheFactor1 in #2227: on an install where Convert Library had never run,
``GET /convert-library-status`` returned 500 (``FileNotFoundError`` on
``/config/convert-library.log``) on every poll of its admin page. The EPUB Fixer had the
same unguarded ``open()``. The cover-enforcer page already read its log through
``_read_log_tail()`` - "" when absent, and a bounded tail rather than the whole file -
so the three siblings disagreed on both points.

These tests drive the real view functions, registered on a Flask app, against an empty
config directory, so each case fails on the code that 500s and passes on the code that
does not.
"""

from __future__ import annotations

import inspect
import json

import flask
import pytest

pytestmark = pytest.mark.unit

SERVICES = [
    pytest.param(
        "convert_library.get_status", "convert-library.log",
        "is_convert_library_finished", "NextGen Convert Library Service - Run Ended: ",
        id="convert-library"),
    pytest.param(
        "epub_fixer.get_status", "epub-fixer.log",
        "is_epub_fixer_finished", "NextGen Kindle EPUB Fixer Service - Run Ended: ",
        id="epub-fixer"),
    pytest.param(
        "cover_enforcer_ui.get_status", "cover-enforcer.log",
        "is_cover_enforcer_finished", "NextGen Cover & Metadata Enforcement Service - Run Ended: ",
        id="cover-enforcer"),
]


@pytest.fixture
def cwaf(tmp_path, monkeypatch):
    """cps.cwa_functions with its config directory pointed at an empty tmp dir."""
    from cps import constants
    import cps.cwa_functions as module

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    return module


@pytest.fixture
def app(cwaf):
    app = flask.Flask(__name__)
    for blueprint in (cwaf.convert_library, cwaf.epub_fixer, cwaf.cover_enforcer_ui):
        app.register_blueprint(blueprint)
    return app


def _poll(app, endpoint):
    """Call the status view the page polls, past its login/admin decorators."""
    view = inspect.unwrap(app.view_functions[endpoint])
    with app.test_request_context():
        resp = view()
        body = resp.get_data(as_text=True) if isinstance(resp, flask.Response) else resp
    return json.loads(body)


@pytest.mark.parametrize("endpoint,log_name,finished,marker", SERVICES)
def test_status_before_the_first_run_is_empty_not_a_500(app, endpoint, log_name, finished, marker, tmp_path):
    assert not (tmp_path / log_name).exists()

    body = _poll(app, endpoint)

    assert body == {"status": "", "progress": {"current": 0, "total": 0}}


@pytest.mark.parametrize("endpoint,log_name,finished,marker", SERVICES)
def test_a_service_that_never_ran_is_not_finished(cwaf, endpoint, log_name, finished, marker, tmp_path):
    assert not (tmp_path / log_name).exists()

    assert getattr(cwaf, finished)() is False


@pytest.mark.parametrize("endpoint,log_name,finished,marker", SERVICES)
def test_a_long_run_is_polled_as_a_bounded_tail(app, cwaf, endpoint, log_name, finished, marker, tmp_path):
    """The poll returns the end of the log, never the whole thing.

    The page re-requests this once a second, and the app runs gevent without
    monkey-patching, so a whole-file read blocks every other request for as long as it
    takes - and the log grows for the length of the run. The progress bar and the
    end-of-run check only ever need the end of the log.
    """
    total = 20_000
    lines = [f"[service]: processing book {i}/{total} - Some Long Book Title Here.epub" for i in range(1, total + 1)]
    (tmp_path / log_name).write_text("\n".join(lines) + f"\n{marker}2026-09-23 10:00:00\n")
    assert (tmp_path / log_name).stat().st_size > 2 * cwaf.SERVICE_STATUS_TAIL_BYTES

    body = _poll(app, endpoint)

    assert len(body["status"].encode("utf-8")) <= cwaf.SERVICE_STATUS_TAIL_BYTES
    assert body["status"].rstrip().endswith("2026-09-23 10:00:00")
    assert body["progress"] == {"current": total, "total": total}
    assert getattr(cwaf, finished)() is True


@pytest.mark.parametrize("endpoint,log_name,finished,marker", SERVICES)
def test_a_run_in_progress_is_not_finished(app, cwaf, endpoint, log_name, finished, marker, tmp_path):
    (tmp_path / log_name).write_text("[service]: processing book 3/7 - Title.epub\n")

    assert _poll(app, endpoint)["progress"] == {"current": 3, "total": 7}
    assert getattr(cwaf, finished)() is False


def test_the_tail_reader_never_exceeds_its_limit(cwaf, tmp_path):
    log = tmp_path / "run.log"
    log.write_bytes(b"Y" * 500_000 + b"TAIL-MARKER")

    out = cwaf._read_log_tail(str(log), limit=1000)

    assert len(out) <= 1000
    assert out.endswith("TAIL-MARKER")


def test_the_tail_reader_tolerates_a_cut_through_a_multibyte_character(cwaf, tmp_path):
    log = tmp_path / "run.log"
    log.write_bytes("é".encode("utf-8") * 50)

    assert isinstance(cwaf._read_log_tail(str(log), limit=5), str)
