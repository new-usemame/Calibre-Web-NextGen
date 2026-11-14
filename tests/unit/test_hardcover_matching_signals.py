# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hardcover auto-match: ISBN, translated titles, subtitles and duplicates.

Real books that the auto-fetch left in the review queue although Hardcover has
them, each pinned here:

* "Les filles de la villa aux étoffes" (Anne Jacobs). Hardcover lists the book
  under its German title and keeps the French one in ``alternative_titles``;
  only the canonical title was compared, so the match scored ~0.55.
* "Play Nice" (Jason Schreier). Hardcover's title carries the subtitle
  ("Play Nice: The Rise, Fall, and Future of Blizzard Entertainment"); the
  Levenshtein ratio against the bare title is ~0.15.
* Books with an ISBN. Search hits carry every edition's ISBN in ``isbns``, but
  the auto-matcher only compared ``identifiers["isbn"]``, which search hits
  never set, so the "ISBN exact match" path could not fire during auto-fetch.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cps.metadata_provider.hardcover import Hardcover
from cps.services.Metadata import MetaRecord, MetaSourceInfo
from cps.tasks import auto_hardcover_id as mod
from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID
from cps.utils.text_similarity import (
    main_title,
    normalize_isbn,
    normalized_levenshtein_similarity,
)

pytestmark = pytest.mark.unit


def _hit(title, authors, *, id_="1", subtitle="", alternative_titles=(), isbns=(),
         users_count=0):
    record = MetaRecord(
        id=id_, title=title, authors=list(authors), url=f"https://hardcover.app/books/{id_}",
        source=MetaSourceInfo("hardcover", "Hardcover", "https://hardcover.app/"),
    )
    record.series = ""
    record.identifiers = {"hardcover-id": id_, "hardcover-slug": f"slug-{id_}"}
    record.match_hints = {
        "subtitle": subtitle,
        "alternative_titles": list(alternative_titles),
        "isbns": list(isbns),
        "users_count": users_count,
    }
    return record


# --- helpers -----------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("978-2-07-036822-8", "9782070368228"),
    ("2-07-036822-X", "9782070368228"),
    ("207036822X", "9782070368228"),
    ("isbn 0441172717", "9780441172719"),
    ("", ""),
    ("not an isbn", ""),
    ("12345", ""),
])
def test_normalize_isbn_compares_isbn10_and_isbn13_forms(raw, expected):
    assert normalize_isbn(raw) == expected


@pytest.mark.parametrize("title, expected", [
    ("Play Nice: The Rise, Fall, and Future of Blizzard Entertainment", "Play Nice"),
    ("Press Reset - Ruin and Recovery", "Press Reset"),
    ("Spider-Man", "Spider-Man"),
    ("Dune", "Dune"),
])
def test_main_title_cuts_at_colon_or_spaced_dash_only(title, expected):
    assert main_title(title) == expected


def test_accents_do_not_lower_similarity():
    assert normalized_levenshtein_similarity(
        "Les filles de la villa aux étoffes", "Les filles de la villa aux etoffes"
    ) == pytest.approx(1.0)


# --- scoring -----------------------------------------------------------------

def test_isbn_from_search_hit_is_an_exact_match():
    hit = _hit("Die Töchter der Tuchvilla", ["Anne Jacobs"],
               isbns=["9783734101540", "2-07-036822-X"])
    score, reason = Hardcover.calculate_confidence_score(
        hit, "Les filles de la villa aux étoffes", query_authors=["Anne Jacobs"],
        query_isbn="9782070368228",
    )
    assert (score, reason) == (1.0, "ISBN exact match")


def test_translated_title_matches_through_alternative_titles():
    hit = _hit("Die Töchter der Tuchvilla", ["Anne Jacobs"],
               alternative_titles=["Les filles de la villa aux étoffes"])
    score, reason = Hardcover.calculate_confidence_score(
        hit, "Les filles de la villa aux étoffes", query_authors=["Anne Jacobs"],
    )
    assert score == pytest.approx(0.95)
    assert "alternative title" in reason


def test_subtitle_kept_in_the_hardcover_title_still_auto_matches():
    hit = _hit("Play Nice: The Rise, Fall, and Future of Blizzard Entertainment",
               ["Jason Schreier"])
    score, reason = Hardcover.calculate_confidence_score(
        hit, "Play Nice", query_authors=["Jason Schreier"],
    )
    assert score == pytest.approx(0.90)
    assert score >= 0.85
    assert "subtitle ignored" in reason


def test_subtitle_kept_in_the_calibre_title_still_auto_matches():
    hit = _hit("Press Reset", ["Jason Schreier"],
               subtitle="Ruin and Recovery in the Video Game Industry")
    score, _ = Hardcover.calculate_confidence_score(
        hit, "Press Reset: Ruin and Recovery in the Video Game Industry",
        query_authors=["Jason Schreier"],
    )
    assert score == pytest.approx(0.95)


def test_subtitle_cut_alone_is_not_enough_without_an_author():
    """The discount keeps a bare main-title hit below the auto-apply line."""
    hit = _hit("Mission: Impossible", [])
    score, _ = Hardcover.calculate_confidence_score(hit, "Mission")
    assert score < 0.85


def test_plain_scores_are_unchanged():
    """No hints, same title: the historical 0.95 (pinned elsewhere too)."""
    hit = _hit("Dune", ["Frank Herbert"])
    score, _ = Hardcover.calculate_confidence_score(hit, "Dune", query_authors=["Frank Herbert"])
    assert score == pytest.approx(0.95)


# --- provider parsing --------------------------------------------------------

def test_search_hits_carry_match_hints_but_not_as_identifiers(monkeypatch):
    from cps.metadata_provider import hardcover as module

    monkeypatch.setattr(module, "current_user", SimpleNamespace(hardcover_token=None))
    monkeypatch.setattr(module, "config", SimpleNamespace(resolved_hardcover_token=lambda: "t"))
    document = {
        "id": 42, "title": "Die Töchter der Tuchvilla", "author_names": ["Anne Jacobs"],
        "slug": "die-tochter-der-tuchvilla", "subtitle": "Roman",
        "alternative_titles": ["Les filles de la villa aux étoffes", ""],
        "isbns": ["9782226396142"], "users_count": 120,
    }
    payload = {"data": {"search": {"results": json.dumps({"hits": [{"document": document}]})}}}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(module.requests, "post", lambda *a, **k: _Response())
    provider = module.Hardcover()
    provider.active = True
    record = provider.search("Les filles de la villa aux étoffes")[0]

    assert record.identifiers == {"hardcover-id": 42, "hardcover-slug": "die-tochter-der-tuchvilla"}
    assert record.match_hints == {
        "subtitle": "Roman",
        "alternative_titles": ["Les filles de la villa aux étoffes"],
        "isbns": ["9782226396142"],
        "users_count": 120,
    }


# --- auto-fetch task ---------------------------------------------------------

def _bare_task(min_confidence=0.85, include_pending=False):
    task = object.__new__(TaskAutoHardcoverID)
    task.log = SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None,
                               warning=lambda *a, **k: None, error=lambda *a, **k: None)
    task.min_confidence = min_confidence
    task.include_pending = include_pending
    task.auto_matched = 0
    task.queued_for_review = 0
    task.skipped_no_results = 0
    task.total_confidence = 0.0
    task.stat = None
    task.QUERY_DELAY = 0
    return task


def _book(title, authors, isbn=None):
    identifiers = [SimpleNamespace(type="isbn", val=isbn)] if isbn else []
    return SimpleNamespace(
        id=7, title=title, series_index=None, series=[], publishers=[], pubdate=None,
        authors=[SimpleNamespace(name=a) for a in authors], identifiers=identifiers,
    )


def _provider(responses, searches):
    class FakeProvider(Hardcover):
        def __init__(self):
            pass

        def search(self, query, *args, **kwargs):
            searches.append(query)
            return responses.get(query, [])

    return FakeProvider


def _capture(monkeypatch, task):
    outcome = {}
    monkeypatch.setattr(task, "_apply_hardcover_id",
                        lambda book_id, result: outcome.update(applied=result.id))
    monkeypatch.setattr(task, "_queue_for_review",
                        lambda *a, **k: outcome.update(queued=a[3]) or True)
    monkeypatch.setattr(task, "_resolve_pending_reviews",
                        lambda book_id: outcome.update(resolved=book_id))
    return outcome


def test_isbn_is_searched_first_and_ends_the_search(monkeypatch):
    searches = []
    translated = _hit("Die Tuchvilla", ["Anne Jacobs"], id_="9", isbns=["9782070368228"])
    monkeypatch.setattr(mod, "Hardcover", _provider({"9782070368228": [translated]}, searches))
    task = _bare_task()
    outcome = _capture(monkeypatch, task)

    task._process_book(_book("La Villa aux étoffes", ["Anne Jacobs"], isbn="2-07-036822-X"))

    assert searches == ["9782070368228"]
    assert outcome["applied"] == "9"


def test_subtitle_retry_runs_only_when_the_first_query_is_not_confident(monkeypatch):
    searches = []
    full = "Press Reset: Ruin and Recovery in the Video Game Industry"
    wrong = _hit("Ruin", ["Someone Else"], id_="1")
    right = _hit("Press Reset", ["Jason Schreier"], id_="2")
    monkeypatch.setattr(mod, "Hardcover", _provider({
        f"{full} Jason Schreier": [wrong],
        "Press Reset Jason Schreier": [right],
    }, searches))
    task = _bare_task()
    outcome = _capture(monkeypatch, task)

    task._process_book(_book(full, ["Jason Schreier"]))

    assert searches == [f"{full} Jason Schreier", "Press Reset Jason Schreier"]
    assert outcome["applied"] == "2"


def test_queue_records_every_query_tried(monkeypatch):
    searches = []
    monkeypatch.setattr(mod, "Hardcover", _provider({
        "Obscure: A Novel Nobody": [_hit("Other", ["Nobody"], id_="3")],
    }, searches))
    task = _bare_task()
    outcome = _capture(monkeypatch, task)

    task._process_book(_book("Obscure: A Novel", ["Nobody"]))

    assert outcome["queued"] == "Obscure: A Novel Nobody | Obscure Nobody"


def test_equal_scores_prefer_the_most_shelved_record(monkeypatch):
    searches = []
    duplicate = _hit("Play Nice", ["Jason Schreier"], id_="11", users_count=2)
    canonical = _hit("Play Nice", ["Jason Schreier"], id_="12", users_count=900)
    monkeypatch.setattr(mod, "Hardcover", _provider({
        "Play Nice Jason Schreier": [duplicate, canonical],
    }, searches))
    task = _bare_task()
    outcome = _capture(monkeypatch, task)

    task._process_book(_book("Play Nice", ["Jason Schreier"]))

    assert outcome["applied"] == "12"


def test_include_pending_only_excludes_rejected_books(monkeypatch):
    seen = {}

    class FakeQuery:
        def filter(self, condition):
            seen["sql"] = str(condition.compile(compile_kwargs={"literal_binds": True}))
            return self

        def distinct(self):
            return self

        def all(self):
            return []

    class FakeSession:
        def query(self, *args):
            return FakeQuery()

        def close(self):
            pass

    monkeypatch.setattr(mod.ub, "init_db_thread", lambda: FakeSession())

    _bare_task(include_pending=False)._get_review_excluded_book_ids()
    assert "reviewed = 0" in seen["sql"]
    _bare_task(include_pending=True)._get_review_excluded_book_ids()
    assert "reviewed = 0" not in seen["sql"]
    assert "'reject'" in seen["sql"]
