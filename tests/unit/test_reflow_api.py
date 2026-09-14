# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Reflow endpoints: what a user is told before they spend, and what stops them.

The estimate is the number a person authorises a payment against, so the tests here
are mostly about the boundary between "we computed this" and "you agreed to that":
consent, the cap, the book that already has an EPUB, and whose sample file it is.
"""
import inspect
import json
import os
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest


# ── harness ──────────────────────────────────────────────────────────────────

def _ctx(path, method="GET", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _user(uid=7, edit=True, admin=False, anon=False, name="ed"):
    return SimpleNamespace(is_authenticated=True, is_anonymous=anon, name=name, id=uid,
                           role_edit=lambda: edit, role_admin=lambda: admin)


def _book(book_id=5, title="A Book", path="Author/Book (5)"):
    return SimpleNamespace(id=book_id, title=title, path=path, authors=[], languages=[],
                           publishers=[], comments=[], tags=[])


def _status(resp):
    return resp[1] if isinstance(resp, tuple) else resp.status_code


def _json(resp):
    body = resp[0] if isinstance(resp, tuple) else resp
    return json.loads(body.get_data(as_text=True))


def _quote(routed=100, pages=400, verdict="TEXT_CLEAN"):
    return {"routed_pages": routed, "pages": pages, "priced_on": "2026-09-12",
            "cheap": round(0.0009 * routed, 4), "standard": round(0.0022 * routed, 4),
            "quality": round(0.0031 * routed, 4),
            "worst_case": {"cheap": round(0.0009 * pages, 4),
                           "standard": round(0.0022 * pages, 4),
                           "quality": round(0.0031 * pages, 4)},
            "sampled": 40, "sampled_routed": 10, "verdict": verdict,
            "text_layer": True, "reasons": {"footnote_markers": 8}}


@pytest.fixture
def mod(tmp_path, monkeypatch):
    from cps.api import reflow as module
    monkeypatch.setattr(module, "REFLOW_DIR", str(tmp_path / "reflow"))
    monkeypatch.setattr(module.tasks_reflow, "REFLOW_DIR", str(tmp_path / "reflow"))
    return module


@pytest.fixture
def pdf_on_disk(tmp_path, monkeypatch):
    """A book whose PDF is where the library says it is."""
    library = tmp_path / "library"
    folder = library / "Author/Book (5)"
    folder.mkdir(parents=True)
    (folder / "Book - Author.pdf").write_bytes(b"%PDF-1.4 not really")
    return {"library": str(library), "name": "Book - Author"}


def _wire(mod, monkeypatch, pdf_on_disk, book=None, epub=False, quote=None,
          key="k", target=0.5, hard_cap=5.0):
    book = book or _book()
    monkeypatch.setattr(mod, "calibre_db", SimpleNamespace(
        get_filtered_book=lambda bid, **kw: book if int(bid) == book.id else None,
        get_book=lambda bid: book if int(bid) == book.id else None,
        get_book_format=lambda bid, fmt: (
            SimpleNamespace(name=pdf_on_disk["name"], format=fmt)
            if (fmt == "PDF" or epub) else None)))
    monkeypatch.setattr(mod, "config", SimpleNamespace(
        get_book_path=lambda: pdf_on_disk["library"],
        resolved_openrouter_key=lambda: key,
        openrouter_key_source=lambda: "database" if key else "",
        config_reflow_default_tier="standard",
        config_reflow_target_usd=target,
        config_reflow_hard_cap_usd=hard_cap))
    monkeypatch.setattr(mod.tasks_reflow, "config", mod.config)
    surveys = []

    def _fake_survey(path):
        surveys.append(path)
        return dict(quote or _quote())

    monkeypatch.setattr(mod, "_survey_uncached", _fake_survey)
    book.surveys = surveys
    return book


# ── the estimate ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_the_estimate_prices_the_pages_the_router_would_actually_send(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk, quote=_quote(routed=100, pages=400))
    with _ctx("/api/v1/books/5/reflow/estimate"):
        with patch.object(mod, "current_user", _user()):
            body = _json(inspect.unwrap(mod.reflow_estimate)(5))

    assert body["routed_pages_estimate"] == 100
    assert body["pages"] == 400
    # 100 routed pages at the measured $0.0022 a page, not a quarter of the book.
    assert body["estimate_usd"]["standard"] == pytest.approx(0.22)
    assert body["worst_case_usd"]["standard"] == pytest.approx(0.88)


@pytest.mark.unit
def test_an_estimate_over_the_target_says_so_and_suggests_a_sample(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk, quote=_quote(routed=500, pages=700), target=0.5)
    with _ctx("/api/v1/books/5/reflow/estimate"):
        with patch.object(mod, "current_user", _user()):
            body = _json(inspect.unwrap(mod.reflow_estimate)(5))

    assert body["estimate_usd"]["standard"] > body["target_usd"]
    assert body["over_target"] is True
    assert body["sample_suggested"] is True


@pytest.mark.unit
def test_the_estimate_is_computed_with_no_key_configured(mod, monkeypatch, pdf_on_disk):
    """The deterministic pass costs nothing, so the price is knowable before setup."""
    _wire(mod, monkeypatch, pdf_on_disk, key="")
    with _ctx("/api/v1/books/5/reflow/estimate"):
        with patch.object(mod, "current_user", _user()):
            body = _json(inspect.unwrap(mod.reflow_estimate)(5))

    assert body["configured"] is False
    assert body["estimate_usd"]["standard"] > 0


@pytest.mark.unit
def test_a_book_that_already_has_an_epub_is_flagged_before_the_user_starts(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk, epub=True)
    with _ctx("/api/v1/books/5/reflow/estimate"):
        with patch.object(mod, "current_user", _user()):
            body = _json(inspect.unwrap(mod.reflow_estimate)(5))
    assert body["existing_epub"] is True


@pytest.mark.unit
def test_a_reader_without_edit_permission_cannot_price_a_conversion(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    with _ctx("/api/v1/books/5/reflow/estimate"):
        with patch.object(mod, "current_user", _user(edit=False)):
            assert _status(inspect.unwrap(mod.reflow_estimate)(5)) == 403


@pytest.mark.unit
def test_a_book_with_no_pdf_has_nothing_to_reflow(mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    monkeypatch.setattr(mod, "calibre_db", SimpleNamespace(
        get_filtered_book=lambda bid, **kw: _book(),
        get_book=lambda bid: _book(),
        get_book_format=lambda bid, fmt: None))
    with _ctx("/api/v1/books/5/reflow/estimate"):
        with patch.object(mod, "current_user", _user()):
            resp = inspect.unwrap(mod.reflow_estimate)(5)
    assert _status(resp) == 404
    assert "PDF" in _json(resp)["error"]["message"]


@pytest.mark.unit
def test_the_second_view_of_the_same_pdf_does_not_read_it_again(
        mod, monkeypatch, pdf_on_disk):
    """A survey is seconds of CPU; the page that shows it is opened repeatedly."""
    wired = _wire(mod, monkeypatch, pdf_on_disk)

    for _ in range(2):
        with _ctx("/api/v1/books/5/reflow/estimate"):
            with patch.object(mod, "current_user", _user()):
                body = _json(inspect.unwrap(mod.reflow_estimate)(5))
    assert len(wired.surveys) == 1
    assert body["routed_pages_estimate"] == 100


@pytest.mark.unit
@pytest.mark.parametrize("what_changed", ["the price table", "the converter"])
def test_a_quote_this_build_would_not_have_made_is_not_served_from_the_cache(
        mod, monkeypatch, pdf_on_disk, what_changed):
    """The estimate is the figure a person authorises a payment against.

    It is cached against the PDF, and the PDF does not change when the app is
    upgraded or the prices are re-measured. A quote kept across either of those is
    last release's money against this release's work.
    """
    wired = _wire(mod, monkeypatch, pdf_on_disk)

    def priced():
        with _ctx("/api/v1/books/5/reflow/estimate"):
            with patch.object(mod, "current_user", _user()):
                return _json(inspect.unwrap(mod.reflow_estimate)(5))

    priced()
    if what_changed == "the price table":
        monkeypatch.setattr(mod.model, "PRICE_TABLE_MEASURED", "2099-01-01")
    else:
        monkeypatch.setattr(mod.build_epub, "CONVERTER_VERSION", "99.0")
    priced()

    assert len(wired.surveys) == 2


@pytest.mark.unit
def test_an_edited_pdf_is_surveyed_again(mod, monkeypatch, pdf_on_disk):
    wired = _wire(mod, monkeypatch, pdf_on_disk)

    source = os.path.join(pdf_on_disk["library"], "Author/Book (5)", "Book - Author.pdf")
    for body_bytes in (b"%PDF-1.4 one", b"%PDF-1.4 a different, longer file"):
        with open(source, "wb") as handle:
            handle.write(body_bytes)
        with _ctx("/api/v1/books/5/reflow/estimate"):
            with patch.object(mod, "current_user", _user()):
                inspect.unwrap(mod.reflow_estimate)(5)
    assert len(wired.surveys) == 2


# ── starting a job ───────────────────────────────────────────────────────────

def _start(mod, body, user=None, tasks=()):
    with _ctx("/api/v1/books/5/reflow", method="POST", body=body):
        with patch.object(mod, "current_user", user or _user()):
            with patch.object(mod.WorkerThread, "get_instance",
                              staticmethod(lambda: SimpleNamespace(tasks=list(tasks)))):
                with patch.object(mod.WorkerThread, "add") as added:
                    resp = inspect.unwrap(mod.reflow_start)(5)
    return resp, added


@pytest.mark.unit
def test_a_conversion_does_not_start_without_consent(mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    resp, added = _start(mod, {"mode": "full", "model_tier": "standard",
                               "cost_cap_usd": 1.0})
    assert _status(resp) == 400
    assert _json(resp)["error"]["code"] == "consent_required"
    added.assert_not_called()


@pytest.mark.unit
def test_a_cap_below_the_estimate_is_refused_rather_than_silently_raised(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk, quote=_quote(routed=100, pages=400))
    resp, added = _start(mod, {"mode": "full", "model_tier": "standard",
                               "consent": True, "cost_cap_usd": 0.10})
    assert _status(resp) == 400
    assert _json(resp)["error"]["code"] == "cap_below_estimate"
    assert "0.22" in _json(resp)["error"]["message"]
    added.assert_not_called()


@pytest.mark.unit
def test_a_sample_is_priced_as_a_sample_and_not_as_the_whole_book(
        mod, monkeypatch, pdf_on_disk):
    """A 20-page look at a book that would cost $0.22 must not demand a $0.22 cap."""
    _wire(mod, monkeypatch, pdf_on_disk, quote=_quote(routed=100, pages=400))
    resp, added = _start(mod, {"mode": "sample", "sample_pages": 20,
                               "model_tier": "standard", "consent": True,
                               "cost_cap_usd": 0.05})
    assert _status(resp) == 202
    added.assert_called_once()


@pytest.mark.unit
def test_a_started_job_carries_the_book_and_what_was_consented_to(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    resp, added = _start(mod, {"mode": "full", "model_tier": "quality", "consent": True,
                               "cost_cap_usd": 1.0, "show_cost_in_report": True})
    assert _status(resp) == 202
    task = added.call_args[0][1]
    assert task.book_id == 5
    assert task.user_id == 7
    assert task.options.model_tier == "quality"
    assert task.options.cost_cap_usd == 1.0
    assert task.options.show_cost_in_report is True
    assert _json(resp)["job_id"] == task.job_id


@pytest.mark.unit
def test_a_cap_above_the_administrators_ceiling_is_brought_back_down(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk, hard_cap=2.0)
    resp, added = _start(mod, {"mode": "full", "model_tier": "standard", "consent": True,
                               "cost_cap_usd": 40.0})
    assert _status(resp) == 202
    assert added.call_args[0][1].options.cost_cap_usd == 2.0


@pytest.mark.unit
def test_a_second_conversion_of_the_same_book_is_refused_while_one_is_running(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    running = SimpleNamespace(book_id=5, stat=mod.STAT_STARTED, id="t1",
                              is_reflow=True)
    resp, added = _start(mod, {"mode": "full", "model_tier": "standard", "consent": True,
                               "cost_cap_usd": 1.0},
                         tasks=[(0, "ed", 0, running, False)])
    assert _status(resp) == 409
    added.assert_not_called()


@pytest.mark.unit
def test_a_finished_conversion_does_not_block_the_next_one(
        mod, monkeypatch, pdf_on_disk):
    from cps.services.worker import STAT_FINISH_SUCCESS

    _wire(mod, monkeypatch, pdf_on_disk)
    done = SimpleNamespace(book_id=5, stat=STAT_FINISH_SUCCESS, id="t1",
                           is_reflow=True)
    resp, _added = _start(mod, {"mode": "full", "model_tier": "standard",
                                "consent": True, "cost_cap_usd": 1.0},
                          tasks=[(0, "ed", 0, done, False)])
    assert _status(resp) == 202


@pytest.mark.unit
def test_a_conversion_cannot_start_with_no_key_configured(
        mod, monkeypatch, pdf_on_disk):
    """Starting would burn the wait and produce a deterministic-only book."""
    _wire(mod, monkeypatch, pdf_on_disk, key="")
    resp, added = _start(mod, {"mode": "full", "model_tier": "standard", "consent": True,
                               "cost_cap_usd": 1.0})
    assert _status(resp) == 400
    assert _json(resp)["error"]["code"] == "not_configured"
    added.assert_not_called()


@pytest.mark.unit
def test_a_reader_without_edit_permission_cannot_spend_the_instances_money(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    resp, added = _start(mod, {"mode": "full", "model_tier": "standard", "consent": True,
                               "cost_cap_usd": 1.0}, user=_user(edit=False))
    assert _status(resp) == 403
    added.assert_not_called()


# ── the sample file ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_a_sample_belongs_to_the_user_who_paid_for_it(mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    mine = mod.tasks_reflow.sample_path(7, "abc123")
    os.makedirs(os.path.dirname(mine), exist_ok=True)
    with open(mine, "wb") as handle:
        handle.write(b"PK\x03\x04sample")

    with _ctx("/api/v1/books/5/reflow/jobs/abc123/sample.epub"):
        with patch.object(mod, "current_user", _user(uid=7)):
            ok = inspect.unwrap(mod.reflow_sample)(5, "abc123")
    assert _status(ok) == 200

    with _ctx("/api/v1/books/5/reflow/jobs/abc123/sample.epub"):
        with patch.object(mod, "current_user", _user(uid=9)):
            theirs = inspect.unwrap(mod.reflow_sample)(5, "abc123")
    assert _status(theirs) == 404


@pytest.mark.unit
@pytest.mark.parametrize("job_id", ["../../../../etc/passwd", "..", "a/b", "x" * 80])
def test_a_job_id_that_is_a_path_is_not_a_job_id(mod, monkeypatch, pdf_on_disk, job_id):
    _wire(mod, monkeypatch, pdf_on_disk)
    with _ctx("/api/v1/books/5/reflow/jobs/x/sample.epub"):
        with patch.object(mod, "current_user", _user()):
            assert _status(inspect.unwrap(mod.reflow_sample)(5, job_id)) == 404


# ── the job list ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_the_job_list_shows_what_each_run_cost_and_how_it_ended(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    from cps.services.reflow import ledger as ledger_mod

    path = os.path.join(mod.REFLOW_DIR, "jobs", "5", "a1b2c3d4e5f60001.jsonl")
    book_ledger = ledger_mod.Ledger(path, cap_usd=1.0, job_id="a1b2c3d4e5f60001")
    book_ledger.record({"kind": "job", "event": "start", "mode": "sample",
                        "user_id": 7, "tier": "standard", "pages": 20})
    book_ledger.record({"kind": "page", "page": 3, "cost_usd": 0.0022, "gate": "PASS",
                        "model": "deepseek/deepseek-v4.1-flash"})
    book_ledger.record({"kind": "page", "page": 4, "cost_usd": 0.0022, "gate": "FAIL",
                        "model": "deepseek/deepseek-v4.1-flash"})
    book_ledger.record({"kind": "job", "event": "finish", "status": "done"})

    with _ctx("/api/v1/books/5/reflow/jobs"):
        with patch.object(mod, "current_user", _user(uid=7)):
            with patch.object(mod.WorkerThread, "get_instance",
                              staticmethod(lambda: SimpleNamespace(tasks=[]))):
                body = _json(inspect.unwrap(mod.reflow_jobs)(5))

    row = body["items"][0]
    assert row["job_id"] == "a1b2c3d4e5f60001"
    assert row["status"] == "done"
    assert row["mode"] == "sample"
    assert row["spend_usd"] == pytest.approx(0.0044)
    assert row["gate"] == {"PASS": 1, "FAIL": 1}
    assert row["sample_url"].endswith("/reflow/jobs/a1b2c3d4e5f60001/sample.epub")


@pytest.mark.unit
def test_a_running_job_is_listed_with_its_progress(mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    task = SimpleNamespace(book_id=5, stat=mod.STAT_STARTED, id="t9", progress=0.25,
                           message="page 12 of 40 · $0.02", job_id="b1b2c3d4e5f60002",
                           is_cancellable=True, is_reflow=True)
    with _ctx("/api/v1/books/5/reflow/jobs"):
        with patch.object(mod, "current_user", _user(uid=7)):
            with patch.object(mod.WorkerThread, "get_instance", staticmethod(
                    lambda: SimpleNamespace(tasks=[(0, "ed", 0, task, False)]))):
                body = _json(inspect.unwrap(mod.reflow_jobs)(5))

    assert body["active"][0]["task_id"] == "t9"
    assert body["active"][0]["progress"] == pytest.approx(0.25)
    assert "page 12 of 40" in body["active"][0]["message"]


@pytest.mark.unit
def test_a_task_the_worker_has_finished_with_is_not_still_active(
        mod, monkeypatch, pdf_on_disk):
    """The worker keeps finished tasks in its list until somebody clears them.

    The page reads ``active`` as "something is running right now": it hides the
    start button and shows a progress bar for every entry. A task that finished
    an hour ago would leave a full bar on the page and the button disabled, while
    the start endpoint -- which looks only at waiting and started tasks -- would
    have accepted the next conversion happily.
    """
    from cps.services.worker import STAT_FINISH_SUCCESS

    _wire(mod, monkeypatch, pdf_on_disk)
    done = SimpleNamespace(book_id=5, stat=STAT_FINISH_SUCCESS, id="t8",
                           progress=1.0, message="done", job_id="d1b2c3d4e5f60004",
                           is_cancellable=False, is_reflow=True)
    running = SimpleNamespace(book_id=5, stat=mod.STAT_WAITING, id="t9", progress=0.0,
                              message="queued", job_id="d1b2c3d4e5f60005",
                              is_cancellable=True, is_reflow=True)
    with _ctx("/api/v1/books/5/reflow/jobs"):
        with patch.object(mod, "current_user", _user(uid=7)):
            with patch.object(mod.WorkerThread, "get_instance", staticmethod(
                    lambda: SimpleNamespace(tasks=[(0, "ed", 0, done, False),
                                                   (0, "ed", 0, running, False)]))):
                body = _json(inspect.unwrap(mod.reflow_jobs)(5))

    assert [row["task_id"] for row in body["active"]] == ["t9"]


@pytest.mark.unit
def test_another_users_job_is_not_in_my_list(mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    from cps.services.reflow import ledger as ledger_mod

    path = os.path.join(mod.REFLOW_DIR, "jobs", "5", "c1b2c3d4e5f60003.jsonl")
    other = ledger_mod.Ledger(path, cap_usd=1.0, job_id="c1b2c3d4e5f60003")
    other.record({"kind": "job", "event": "start", "mode": "full", "user_id": 99})

    with _ctx("/api/v1/books/5/reflow/jobs"):
        with patch.object(mod, "current_user", _user(uid=7)):
            with patch.object(mod.WorkerThread, "get_instance",
                              staticmethod(lambda: SimpleNamespace(tasks=[]))):
                body = _json(inspect.unwrap(mod.reflow_jobs)(5))
    assert body["items"] == []

    with _ctx("/api/v1/books/5/reflow/jobs"):
        with patch.object(mod, "current_user", _user(uid=7, admin=True)):
            with patch.object(mod.WorkerThread, "get_instance",
                              staticmethod(lambda: SimpleNamespace(tasks=[]))):
                admin_body = _json(inspect.unwrap(mod.reflow_jobs)(5))
    assert [row["job_id"] for row in admin_body["items"]] == ["c1b2c3d4e5f60003"]


# ── the report of the book that is in the library ────────────────────────────

def _epub_with_sidecar(path, payload):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", "<container/>")
        if payload is not None:
            zf.writestr("META-INF/reflow.json", json.dumps(payload))


@pytest.mark.unit
def test_the_book_page_can_read_back_the_conversions_own_numbers(
        mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk, epub=True)
    target = os.path.join(pdf_on_disk["library"], "Author/Book (5)",
                          pdf_on_disk["name"] + ".epub")
    _epub_with_sidecar(target, {"converter": "Reflow", "fidelity": {"pages": 400}})

    with _ctx("/api/v1/books/5/reflow/report"):
        with patch.object(mod, "current_user", _user()):
            body = _json(inspect.unwrap(mod.reflow_report)(5))
    assert body["converter"] == "Reflow"
    assert body["fidelity"]["pages"] == 400


@pytest.mark.unit
def test_an_epub_that_reflow_did_not_make_has_no_report(mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk, epub=True)
    target = os.path.join(pdf_on_disk["library"], "Author/Book (5)",
                          pdf_on_disk["name"] + ".epub")
    _epub_with_sidecar(target, None)

    with _ctx("/api/v1/books/5/reflow/report"):
        with patch.object(mod, "current_user", _user()):
            assert _status(inspect.unwrap(mod.reflow_report)(5)) == 404


# ── the administrator's settings ─────────────────────────────────────────────

@pytest.mark.unit
def test_the_key_is_never_echoed_back_to_the_browser(mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk, key="sk-or-v1-REALSECRET")
    with _ctx("/api/v1/admin/reflow"):
        with patch.object(mod, "current_user", _user(admin=True)):
            body = _json(inspect.unwrap(mod.reflow_admin_config)())

    assert body["configured"] is True
    assert body["key_source"] == "database"
    assert "REALSECRET" not in json.dumps(body)


@pytest.mark.unit
def test_only_an_administrator_sees_the_reflow_settings(mod, monkeypatch, pdf_on_disk):
    _wire(mod, monkeypatch, pdf_on_disk)
    with _ctx("/api/v1/admin/reflow"):
        with patch.object(mod, "current_user", _user(admin=False)):
            assert _status(inspect.unwrap(mod.reflow_admin_config)()) == 403
