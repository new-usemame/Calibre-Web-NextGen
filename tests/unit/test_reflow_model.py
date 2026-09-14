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


def _prompt_of(history):
    """The text half of the user message that actually went out."""
    return history[0].json()["messages"][1]["content"][-1]["text"]


def test_the_headings_the_reader_measured_go_out_with_the_page():
    """The model is told what the page sets as a heading rather than asked to find
    them: MEASURED on page index 102 of the acceptance book, a model left to decide
    made <h2> of three items of a numbered list, which is three chapters of the
    finished EPUB. A rule that only lives in the gate refuses pages; a rule the model
    is told lets it answer."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        _edit(_client(), headings=[(2, "Serapio of Alexandria (First Century CE?)")])
        prompt = _prompt_of(m.request_history)

    assert "<h2> Serapio of Alexandria (First Century CE?)" in prompt, prompt


def test_a_page_that_sets_no_heading_is_told_that_too():
    """Silence would read as 'you decide', which is the failure this closes."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        _edit(_client(), headings=[])
        prompt = _prompt_of(m.request_history)

    assert "sets no heading" in prompt, prompt


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


def test_an_answer_we_cannot_use_still_carries_what_it_cost():
    """The provider answered, so the provider billed: the tokens are on the usage
    block of a 200 whatever the content turned out to be. The page keeps its
    deterministic text, and the charge is still real, so it travels with the
    exception to whoever is counting money."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply(content="I can't transcribe this page: it is blank.",
                                     usage={"prompt_tokens": 1500, "completion_tokens": 12,
                                            "cost": 0.0031}))
        with pytest.raises(model.UnusableAnswer) as caught:
            _edit(_client())

    assert caught.value.cost_usd == pytest.approx(0.0031)
    assert caught.value.cost_source == "provider"
    assert caught.value.prompt_tokens == 1500
    assert caught.value.completion_tokens == 12


def test_an_answer_cut_off_at_the_token_ceiling_carries_what_it_cost():
    """The expensive half of the same case: a truncated answer burned the whole
    completion ceiling before it was thrown away."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json={"id": "gen-1", "model": "deepseek/deepseek-v4.1-flash",
                               "choices": [{"message": {"content": "<p>half a pa"},
                                            "finish_reason": "length"}],
                               "usage": {"prompt_tokens": 1500, "completion_tokens": 3840,
                                         "cost": 0.0042}})
        with pytest.raises(model.UnusableAnswer) as caught:
            _edit(_client())

    assert caught.value.cost_usd == pytest.approx(0.0042)


def test_a_provider_that_would_not_answer_at_all_carries_no_cost():
    """The control. A 400 produced no tokens and no bill, and a cost invented for it
    would charge the reader's cap for a call that never happened."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, status_code=400, json={"error": {"message": "bad model"}})
        with pytest.raises(model.ModelError) as caught:
            _edit(_client(backoff=0.0))

    assert getattr(caught.value, "cost_usd", 0.0) == 0.0


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


def test_an_answer_the_provider_cut_short_is_an_error_and_not_half_a_page():
    """MEASURED on the acceptance book: page 100 came back at exactly the completion
    ceiling, missing 182 words and five closing asides. Parsed as an answer it looks
    like a model that deleted a third of the page, the gate refuses it for the wrong
    reason, and the money is spent either way. The provider already said what
    happened; this makes the pipeline say it too."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json={"id": "gen-1", "model": "deepseek/deepseek-v4.1-flash",
                               "choices": [{"message": {"content": PAGE_HTML[:60]},
                                            "finish_reason": "length"}],
                               "usage": {"prompt_tokens": 1200, "completion_tokens": 4096}})
        with pytest.raises(model.ModelError) as raised:
            _edit(_client())

    assert "cut" in str(raised.value).lower() or "truncat" in str(raised.value).lower()


def test_a_dense_page_is_given_room_to_come_back_whole():
    """The answer is the page again with markup around it, so the ceiling has to be
    a function of the page rather than a constant somebody picked once."""
    sent = []
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        client = _client()
        client.edit_page(page_text="word " * 40, image_jpeg=None, ladder=(1,))
        sent.append(m.last_request.json()["max_tokens"])
        client.edit_page(page_text="word " * 4000, image_jpeg=None, ladder=(1,))
        sent.append(m.last_request.json()["max_tokens"])

    assert sent[1] > sent[0], sent
    assert sent[0] >= 2048, "even a thin page needs room for its markup"


def test_the_request_does_not_pay_for_thinking_nobody_reads():
    """MEASURED: with reasoning left on, the same page cost 0.0054 and came back
    truncated; with it off, 0.0021 and complete. Re-marking text that is already in
    front of the model is not a reasoning problem, and the reasoning tokens are
    billed as completion tokens whether or not anyone can see them."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply())
        _edit(_client())
        body = m.last_request.json()

    assert body.get("reasoning", {}).get("enabled") is False, body.get("reasoning")


def test_the_answer_records_which_provider_served_it():
    """MEASURED: the same model on the same day billed $0.30/$1.20 per million from
    one provider and $0.375/$1.50 from another, and answered differently. A ledger
    that cannot say which one served a page cannot answer either question."""
    with requests_mock.Mocker() as m:
        payload = _reply()
        payload["provider"] = "Venice"
        m.post(ENDPOINT, json=payload)
        result = _edit(_client())

    assert result.provider == "Venice"


def test_the_contract_line_is_parsed_off_the_html():
    """The prompt requires a trailing JSON object naming what the model was unsure
    of. That is metadata, and it must not end up in the reader's book."""
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply(
            content=PAGE_HTML + '\n{"uncertain": ["marker after \'caution.\'"]}'))
        result = _edit(_client())

    assert result.uncertain == [{"token": "marker after 'caution.'",
                                 "candidates": []}]
    assert "uncertain" not in result.html


def test_a_flagged_reading_arrives_in_one_shape_whatever_the_model_called_it():
    """Everything downstream -- the report list, the inline annotation -- has to
    find the damaged token and its candidate readings without knowing which model
    answered. The prompt asks for {"token", "candidates"} and DeepSeek obliges
    (28 of 28 records measured on book 567), but the key names are the model's
    choice, not ours, and a record nobody can read is a reading nobody checks.
    """
    line = ('{"uncertain": [{"token": "4s", "candidates": ["45", "4s"]}, '
            '{"text": "Po\u00e8me", "alternatives": ["Po\u00e8me", "Poeme"]}, '
            '{"word": "mathematike"}], "notes": ""}')
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply(content=PAGE_HTML + "\n" + line))
        result = _edit(_client())

    assert result.uncertain == [
        {"token": "4s", "candidates": ["45", "4s"]},
        {"token": "Po\u00e8me", "candidates": ["Po\u00e8me", "Poeme"]},
        {"token": "mathematike", "candidates": []},
    ]


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


# ------------------------------------------------ the price the user was quoted

def test_no_upstream_that_charges_more_than_the_quote_can_serve_the_request():
    """The estimate is a ceiling the request enforces, not a guess about routing.

    MEASURED 2026-09-13: deepseek-v4.1-flash is served by fourteen upstreams on
    OpenRouter charging between $0.15 and $0.375 per million prompt tokens, and
    default routing picks a different one per request -- three consecutive pages of
    the acceptance book were billed by DeepInfra, GMICloud and Venice at $0.00093,
    $0.00148 and $0.00270 for the same work. A figure computed from one of those and
    billed at another is not a figure anybody can consent to, and the difference is
    the job stopping half way through the book on a cap the user thought was
    generous. So every request carries the quoted rate as a hard price ceiling.
    """
    for tier, spec in model.TIERS.items():
        with requests_mock.Mocker() as m:
            m.post(ENDPOINT, json=_reply())
            _edit(_client(tier=tier))
            sent = m.last_request.json()

        ceiling = (sent.get("provider") or {}).get("max_price") or {}
        assert ceiling.get("prompt") == pytest.approx(spec.prompt_usd_per_mtok), tier
        assert ceiling.get("completion") == pytest.approx(spec.completion_usd_per_mtok), tier


def test_the_page_price_the_estimate_multiplies_is_the_ceiling_rate():
    """What a page costs and what a page is allowed to cost are one number.

    A tier whose per-page figure was written by hand can drift below its own price
    ceiling, and then the estimate understates the bill by our arithmetic rather than
    the provider's -- which is the same failure with nobody to blame it on.
    """
    for tier, spec in model.TIERS.items():
        worst = (model.PAGE_PROMPT_TOKENS * spec.prompt_usd_per_mtok
                 + model.PAGE_COMPLETION_TOKENS * spec.completion_usd_per_mtok) / 1e6
        assert spec.price_per_page == pytest.approx(worst, rel=1e-6), tier


def test_a_note_that_came_back_without_its_number_comes_out_with_it():
    """The answer is put into one shape here, so the gate, the cache and the EPUB all
    see the same note. MEASURED on the acceptance book: providers differ on whether
    the number goes in the aside's text or only in its id, and a reader's EPUB with
    unnumbered notes is a worse outcome than a refused page."""
    bare = ('<p>text of the page<a class="noteref" href="#fn_160">160</a></p>'
            '<aside class="footnote" id="fn_160">Beck, p. 12.</aside>')
    with requests_mock.Mocker() as m:
        m.post(ENDPOINT, json=_reply(content=bare))
        result = _edit(_client())

    assert '<aside class="footnote" id="fn_160">160 Beck, p. 12.</aside>' in result.html
