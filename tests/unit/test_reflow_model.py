# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The only part of Reflow that spends the user's money or leaves the machine.

Three things have to hold no matter what the provider does: the spend is counted
against a cap that is checked *before* the request goes out, every request carries an
explicit timeout (this app runs under gevent — a socket with no deadline parks a
worker forever), and an answer we cannot parse is an error rather than an empty page
that would silently delete the reader's text.
"""

import json

import pytest
import requests_mock

from cps.services.reflow import ledger as ledger_mod
from cps.services.reflow import model

pytestmark = pytest.mark.unit

ENDPOINT = model.OPENROUTER_URL
FAKE_KEY = "sk-or-v1-not-a-real-key-0000"

PAGE_HTML = ('<h3>Serapio of Alexandria</h3>\n<p>text of the page'
             '<a class="noteref" href="#fn_160">160</a></p>\n'
             '<aside class="footnote" id="fn_160">Beck.</aside>')


def _reply(content=PAGE_HTML, usage=None, model_id="deepseek/deepseek-v4.1-flash"):
    return {"id": "gen-1", "model": model_id,
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 1200, "completion_tokens": 800}}


def _client(**kwargs):
    kwargs.setdefault("tier", "standard")
    return model.OpenRouterClient(api_key=FAKE_KEY, **kwargs)


def _edit(client, **kwargs):
    return client.edit_page(page_text="text of the page[160]",
                            image_jpeg=b"\xff\xd8fake", ladder=(1, 2, 3), **kwargs)


# --------------------------------------------------------------------- the money

def test_token_usage_becomes_dollars_from_the_measured_price_table():
    """A per-call cost nobody can recompute is a number nobody can audit."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        result = _edit(_client())

    spec = model.TIERS["standard"]
    expected = (1200 * spec.prompt_usd_per_mtok + 800 * spec.completion_usd_per_mtok) / 1e6

    assert result.cost_usd == pytest.approx(expected, rel=1e-9)
    assert result.cost_source == "price_table"


def test_the_providers_own_cost_wins_when_it_reports_one():
    """OpenRouter bills by its own meter, including provider surcharges the table
    cannot know about. Where it tells us, that is the truth."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply(usage={"prompt_tokens": 10, "completion_tokens": 10,
                                            "cost": 0.0042}))
        result = _edit(_client())

    assert result.cost_usd == pytest.approx(0.0042)
    assert result.cost_source == "provider"


def test_a_call_that_would_break_the_cap_never_reaches_the_network(tmp_path):
    """G5. The check has to happen before the request, not after the bill."""
    book = ledger_mod.Ledger(tmp_path / "job.jsonl", cap_usd=0.01)
    book.record({"page": 1, "cost_usd": 0.0099})

    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        with pytest.raises(model.CapExceeded):
            _edit(_client(), ledger=book)

        assert m.call_count == 0


def test_the_cap_lets_an_affordable_call_through(tmp_path):
    """A cap that refuses everything would pass the test above and be useless."""
    book = ledger_mod.Ledger(tmp_path / "job.jsonl", cap_usd=5.0)

    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        result = _edit(_client(), ledger=book)

    assert result.cost_usd > 0
    assert m.call_count == 1


# ------------------------------------------------------------------- the network

def test_every_request_carries_an_explicit_timeout():
    """Under gevent a request with no deadline parks a worker until the process
    dies. There is no global default that saves us."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        _edit(_client())

        assert m.last_request.timeout == _client().timeout
        assert all(isinstance(v, (int, float)) and v > 0 for v in m.last_request.timeout)


def test_a_rate_limited_call_is_retried_and_then_succeeds():
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, [{"status_code": 429, "json": {"error": "slow down"}},
                          {"status_code": 200, "json": _reply()}])
        result = _edit(_client(backoff=0.0))

    assert result.attempts == 2
    assert m.call_count == 2


def test_a_rejected_request_is_not_retried():
    """Retrying a 400 burns the cap on an answer that will never come."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, status_code=400, json={"error": {"message": "bad model"}})
        with pytest.raises(model.ModelError):
            _edit(_client(backoff=0.0))

        assert m.call_count == 1


def test_the_api_key_is_not_in_the_objects_repr():
    """Reflow's errors go to the application log and into task messages."""
    client = _client()

    assert FAKE_KEY not in repr(client)
    assert FAKE_KEY not in str(client.describe())


def test_a_dry_run_spends_nothing_and_calls_nothing():
    """The estimate path and the tests must be able to exercise the pipeline with no
    key configured at all."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        result = model.OpenRouterClient(api_key=None, dry_run=True).edit_page(
            page_text="text of the page[160]", image_jpeg=None, ladder=(1,))

        assert m.call_count == 0
    assert result.cost_usd == 0.0
    assert result.html


def test_an_answer_with_no_usable_html_is_an_error_not_an_empty_page():
    """An empty string would pass a word-preservation gate that only counts what is
    there — and would delete the page."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply(content="I'm sorry, I can't help with that."))
        with pytest.raises(model.ModelError):
            _edit(_client(backoff=0.0))


def test_the_contract_line_is_parsed_off_the_html():
    """The prompt requires a trailing JSON object naming what the model was unsure
    of. That is metadata, and it must not end up in the reader's book."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply(
            content=PAGE_HTML + '\n{"uncertain": ["marker after \'caution.\'"]}'))
        result = _edit(_client())

    assert result.uncertain == ["marker after 'caution.'"]
    assert "uncertain" not in result.html


# ------------------------------------------------------------------- the ledger

def test_the_ledger_totals_what_it_recorded(tmp_path):
    book = ledger_mod.Ledger(tmp_path / "job.jsonl", cap_usd=1.0)
    book.record({"page": 1, "cost_usd": 0.002, "gate": "PASS"})
    book.record({"page": 2, "cost_usd": 0.003, "gate": "FAIL"})

    totals = book.totals()

    assert totals["spend_usd"] == pytest.approx(0.005)
    assert totals["calls"] == 2
    assert totals["gate"]["PASS"] == 1


def test_a_ledger_reopened_after_a_crash_remembers_the_spend(tmp_path):
    """Resumability is the reason this is a file and not a variable: a re-run after a
    crash must not spend the cap twice."""
    path = tmp_path / "job.jsonl"
    first = ledger_mod.Ledger(path, cap_usd=1.0)
    first.record({"page": 1, "cost_usd": 0.25})

    second = ledger_mod.Ledger(path, cap_usd=1.0)

    assert second.spent() == pytest.approx(0.25)
    assert second.remaining() == pytest.approx(0.75)


def test_the_ledger_writes_one_json_object_per_line(tmp_path):
    """The file is read by humans during an incident and by the report page after."""
    path = tmp_path / "job.jsonl"
    book = ledger_mod.Ledger(path, cap_usd=1.0)
    book.record({"page": 1, "cost_usd": 0.002})
    book.record({"page": 2, "cost_usd": 0.002})

    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    assert [entry["page"] for entry in lines] == [1, 2]
