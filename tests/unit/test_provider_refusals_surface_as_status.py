# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A source that refused the request is reported as refused, not as "No results".

Household instance, 2026-09-10, one cover-picker search: Hardcover answered
HTTP 401 (the stored token is rejected), Google Books HTTP 429 (shared-IP
quota, no API key), ComicVine HTTP 420 (shared key throttled), Amazon HTTP 503.
Each provider logged a warning and returned an empty list, so the picker and
the metadata modal showed all four as "No results for this query" — the same
words a working source uses for a book it does not stock. The user could not
tell a misconfigured key from a missing book, and neither could the log, which
carried no per-search summary.

Providers now raise ``ProviderRefused`` with the status the UI already knows
(``missing_key`` / ``rate_limited`` / ``blocked``) and a remedy in the message;
the fan-out consumers classify it, and one INFO line per search names every
source with its outcome.
"""
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import types

import pytest
import requests

pytestmark = pytest.mark.unit


def _http_error(status):
    err = requests.exceptions.HTTPError(f"{status} Client Error: for url: x")
    err.response = types.SimpleNamespace(status_code=status)
    return err


def _raise(exc):
    def _inner(*_a, **_k):
        raise exc
    return _inner


def test_hardcover_rejected_token_is_a_missing_key_refusal(monkeypatch):
    from cps.metadata_provider import hardcover as hc
    from cps.services.Metadata import ProviderRefused

    monkeypatch.setattr(hc, "current_user", types.SimpleNamespace(hardcover_token="BAD1"))
    monkeypatch.setattr(hc, "config", types.SimpleNamespace(resolved_hardcover_token=lambda: "BAD2"))
    monkeypatch.setattr(hc.requests, "post", _raise(_http_error(401)))
    provider = hc.Hardcover()
    provider.active = True

    with pytest.raises(ProviderRefused) as refused:
        provider.search("dune")
    assert refused.value.status == "missing_key"
    assert "hardcover.app/account/api" in str(refused.value)


def test_google_quota_is_a_rate_limit_refusal_naming_the_key(monkeypatch):
    from cps.metadata_provider import google
    from cps.services.Metadata import ProviderRefused

    monkeypatch.setattr(google, "config", types.SimpleNamespace(config_google_books_api_key=None))
    monkeypatch.setattr(google.requests, "get", _raise(_http_error(429)))
    provider = google.Google()
    provider.active = True

    with pytest.raises(ProviderRefused) as refused:
        provider.search("dune")
    assert refused.value.status == "rate_limited"
    assert "API key" in str(refused.value)


@pytest.mark.parametrize(("provider_id", "status", "key", "expected"), [
    ("google", 403, None, "blocked"),
    ("google", 403, "fixture-google-key", "missing_key"),
    ("google", 400, "fixture-google-key", "missing_key"),
    ("google", 503, "fixture-google-key", "blocked"),
    ("google", 500, "fixture-google-key", "error"),
    ("openlibrary", 403, None, "blocked"),
    ("openlibrary", 429, None, "rate_limited"),
    ("openlibrary", 503, None, "blocked"),
    ("openlibrary", 500, None, "error"),
    ("openlibrary", None, None, "error"),
    ("dnb", 403, None, "blocked"),
    ("dnb", 429, None, "rate_limited"),
    ("dnb", 503, None, "blocked"),
    ("dnb", None, None, "error"),
])
def test_provider_http_failures_reach_search_status(monkeypatch, caplog, provider_id, status, key, expected):
    from cps.metadata_provider import dnb, google, openlibrary
    from cps.search_metadata import _classify_provider_failure
    from cps.services.cover_picker import gather_cover_candidates

    provider_class = {"dnb": dnb.DNB, "google": google.Google, "openlibrary": openlibrary.OpenLibrary}
    monkeypatch.setattr(google, "config", types.SimpleNamespace(config_google_books_api_key=key))
    provider = provider_class[provider_id]()
    provider.active = True

    def get(url, **_kwargs):
        if status is None:
            raise requests.ConnectionError(f"Connection refused for {url}")
        response = requests.Response()
        response.status_code = status
        response.url = url
        if status == 400:
            response._content = b'{"error":{"details":[{"reason":"API_KEY_INVALID"}]}}'
        return response

    monkeypatch.setattr(requests, "get", get)
    candidates, statuses = gather_cover_candidates(
        providers=[provider], query="Quotas", static_cover="", locale="en",
        classify_failure=_classify_provider_failure,
    )

    assert candidates == []
    assert len(statuses) == 1
    assert statuses[0].status == expected
    assert statuses[0].message
    if key:
        assert key not in caplog.text
        assert key not in statuses[0].message


def test_open_library_no_results_is_distinct_from_invalid_json(monkeypatch):
    from cps.metadata_provider.openlibrary import OpenLibrary
    from cps.search_metadata import _classify_provider_failure
    from cps.services.cover_picker import gather_cover_candidates

    provider = OpenLibrary()
    provider.active = True
    response = requests.Response()
    response.status_code = 200
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: response)

    def outcome(payload):
        response._content = payload
        _, statuses = gather_cover_candidates(
            providers=[provider], query="dune", static_cover="", locale="en",
            classify_failure=_classify_provider_failure,
        )
        return statuses[0].status

    assert outcome(b'{"docs": []}') == "empty"
    assert outcome(b"not json") == "error"


@pytest.mark.parametrize("redirect", [False, True])
def test_google_key_is_carried_outside_the_logged_request_target(monkeypatch, caplog, redirect):
    from cps.metadata_provider import google
    from cps.services.Metadata import ProviderRefused

    key = "fixture-google-key"
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("X-Goog-Api-Key")))
            if redirect and self.path != "/unexpected":
                self.send_response(302)
                self.send_header("Location", "/unexpected")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"items":[]}')

    monkeypatch.setattr(google, "config", types.SimpleNamespace(config_google_books_api_key=key))
    provider = google.Google()
    provider.active = True

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        monkeypatch.setattr(google.Google, "SEARCH_URL", f"http://127.0.0.1:{server.server_port}/volumes?q=")
        try:
            with caplog.at_level(logging.DEBUG, logger="urllib3.connectionpool"):
                if redirect:
                    with pytest.raises(ProviderRefused, match="redirect"):
                        provider.search("dune")
                else:
                    assert provider.search("dune") == []
        finally:
            server.shutdown()
            worker.join()

    assert "GET /volumes?q=dune" in caplog.text
    assert key not in caplog.text
    assert seen == [("/volumes?q=dune", key)]


def test_dnb_keeps_records_after_a_malformed_record(monkeypatch):
    from cps.metadata_provider.dnb import DNB

    provider = DNB()
    provider.active = True
    monkeypatch.setattr(provider, "_execute_query", lambda _query: [1, None, 2])

    def parse(record):
        if record is None:
            raise ValueError("malformed MARC record")
        return {"id": record}

    monkeypatch.setattr(provider, "_parse_marc21_record", parse)
    monkeypatch.setattr(provider, "_create_meta_record", lambda book, _cover: book["id"])

    assert provider.search("dune") == [1, 2]


@pytest.mark.parametrize("key, status, expected", [
    ("", 420, "rate_limited"),            # shared key throttled
    ("", 429, "rate_limited"),
    ("a-wrong-key", 401, "missing_key"),  # the install's own key rejected
])
def test_comicvine_refusals_carry_their_status(monkeypatch, key, status, expected):
    from cps.metadata_provider import comicvine
    from cps.services.Metadata import ProviderRefused

    monkeypatch.setattr(comicvine, "config", types.SimpleNamespace(resolved_comicvine_api_key=lambda: key))
    monkeypatch.setattr(comicvine.requests, "get", _raise(_http_error(status)))
    provider = comicvine.ComicVine()
    provider.active = True

    with pytest.raises(ProviderRefused) as refused:
        provider.search("Batman")
    assert refused.value.status == expected
    assert "Keys panel" in str(refused.value) if not key else "Check the ComicVine API key" in str(refused.value)


def test_amazon_503_is_reported_as_blocked(monkeypatch):
    from cps.metadata_provider import amazon
    from cps.services.Metadata import ProviderRefused

    monkeypatch.setattr(amazon.Amazon.session, "get", _raise(_http_error(503)))
    provider = amazon.Amazon()
    provider.active = True

    with pytest.raises(ProviderRefused) as refused:
        provider.search("dune")
    assert refused.value.status == "blocked"


def test_classifier_passes_a_refusal_through_unchanged():
    from cps.search_metadata import _classify_provider_failure
    from cps.services.Metadata import ProviderRefused

    exc = ProviderRefused("rate_limited", "Rate-limited by this source. Wait a minute and search again.")
    assert _classify_provider_failure(exc, None) == ("rate_limited", str(exc))
    # A keyless scraper's refusal keeps the status the provider chose too.
    assert _classify_provider_failure(exc, types.SimpleNamespace(__id__="goodreads")) == ("rate_limited", str(exc))


class _Provider:
    def __init__(self, pid, outcome):
        self.__id__ = pid
        self.__name__ = pid.title()
        self._outcome = outcome

    def search(self, query, generic_cover="", locale="en"):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def test_picker_reports_the_refusal_and_logs_one_summary_line(monkeypatch):
    from cps.search_metadata import _classify_provider_failure
    from cps.services import cover_picker as svc
    from cps.services.Metadata import MetaRecord, MetaSourceInfo, ProviderRefused

    hit = MetaRecord(
        id="1", title="Nineteen Eighty-Four", authors=["George Orwell"], url="https://good.test/1",
        source=MetaSourceInfo(id="good", description="Good", link="https://good.test"),
        cover="https://good.test/1.jpg",
    )
    providers = [
        _Provider("good", [hit]),
        _Provider("quiet", []),
        _Provider("throttled", ProviderRefused("rate_limited", "Rate-limited by this source.")),
    ]
    seen = []
    monkeypatch.setattr(svc.log, "info", lambda msg, *args: seen.append(msg % args if args else msg))

    candidates, statuses = svc.gather_cover_candidates(
        providers=providers, query="Nineteen Eighty-Four George Orwell",
        static_cover="/static/generic_cover.svg", locale="en",
        classify_failure=_classify_provider_failure,
    )

    by_id = {s.id: s for s in statuses}
    assert by_id["throttled"].status == "rate_limited"
    assert by_id["throttled"].message == "Rate-limited by this source."
    assert by_id["quiet"].status == "empty"
    assert by_id["good"].status == "ok" and by_id["good"].count == 1
    assert [c.source_id for c in candidates] == ["good"]

    summaries = [line for line in seen if "sources answered" in line]
    assert len(summaries) == 1, seen
    line = summaries[0]
    assert "1 of 3 sources answered" in line
    assert "1 candidate" in line
    assert "ok: good=1" in line
    assert "empty: quiet" in line
    assert "rate_limited: throttled" in line
    assert "Nineteen Eighty-Four George Orwell" in line
