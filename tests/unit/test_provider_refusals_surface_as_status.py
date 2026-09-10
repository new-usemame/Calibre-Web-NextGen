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
