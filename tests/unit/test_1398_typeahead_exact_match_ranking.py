# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for typeahead suggestion ranking (#1398).

@magdalar reported that typing an existing tag offers a different tag first:
typing "Romance" pre-selects "Paranormal Romance". Reproduced on a real library
against the live route — `GET /api/v1/metadata/typeahead/tags?q=life` answered
["Conduct of life -- Fiction", "Life", "Spain -- Social life and customs ..."],
so the exact tag was second and the SPA's Enter applied the first one.

Root cause: `get_typeahead` ran `LIKE %q%` with no ORDER BY, so SQLite returned
name-index order and an exact match sorted wherever the alphabet put it. The
editor then kept only the first 25, so on a large library an exact match could
be truncated away before it was ever offered.

Pins: exact > prefix > anywhere ordering, alphabetical tiebreak, case-insensitive
exact match, empty query, no rows dropped, that `get_typeahead` itself applies
the ranking (so the classic editor is ranked too, not just the SPA), and that an
exact match survives the result cap.
"""
import json

import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

try:  # SQLAlchemy 2.x
    from sqlalchemy.orm import declarative_base
except ImportError:  # pragma: no cover - SQLAlchemy 1.3
    from sqlalchemy.ext.declarative import declarative_base


# The exact rows the live library returned for q="life", in the order the
# unranked query produced them. This is the reporter's bug, verbatim.
LIVE_REPRO = ["Conduct of life -- Fiction", "Life",
              "Spain -- Social life and customs -- 16th century -- Fiction"]


# ── the pure ranking helper ──────────────────────────────────────────────────

@pytest.mark.unit
def test_exact_match_ranks_first_live_repro():
    from cps.db import rank_typeahead_names
    assert rank_typeahead_names(LIVE_REPRO, "life")[0] == "Life"


@pytest.mark.unit
def test_prefix_beats_anywhere_match():
    from cps.db import rank_typeahead_names
    ranked = rank_typeahead_names(
        ["Paranormal Romance", "Romance novels", "Historical Romance"], "romance")
    assert ranked[0] == "Romance novels"


@pytest.mark.unit
def test_exact_beats_prefix():
    from cps.db import rank_typeahead_names
    ranked = rank_typeahead_names(["Romance novels", "Romance", "Paranormal Romance"], "romance")
    assert ranked[:2] == ["Romance", "Romance novels"]


@pytest.mark.unit
def test_exact_match_is_case_insensitive_and_keeps_stored_casing():
    from cps.db import rank_typeahead_names
    ranked = rank_typeahead_names(["Epic fantasy", "FANTASY"], "fantasy")
    assert ranked[0] == "FANTASY"


@pytest.mark.unit
def test_ties_are_alphabetical_within_a_rank():
    from cps.db import rank_typeahead_names
    ranked = rank_typeahead_names(["Sci-fi zebra", "Sci-fi apple", "Sci-fi mango"], "sci-fi")
    assert ranked == ["Sci-fi apple", "Sci-fi mango", "Sci-fi zebra"]


@pytest.mark.unit
def test_empty_query_is_alphabetical_and_does_not_raise():
    from cps.db import rank_typeahead_names
    assert rank_typeahead_names(["beta", "Alpha"], "") == ["Alpha", "beta"]
    assert rank_typeahead_names(["beta", "Alpha"], None) == ["Alpha", "beta"]


@pytest.mark.unit
def test_ranking_drops_nothing():
    from cps.db import rank_typeahead_names
    names = ["Life", "Conduct of life", "Still life", "unrelated"]
    assert sorted(rank_typeahead_names(names, "life")) == sorted(names)


@pytest.mark.unit
def test_exact_match_survives_the_result_cap():
    """The editor keeps only the first N. Ranking has to happen before that cut,
    or a library with more than N partial matches never offers the exact tag."""
    from cps.api.edit import _TYPEAHEAD_LIMIT
    from cps.db import rank_typeahead_names
    # 40 anywhere-matches that all sort alphabetically ahead of the exact tag.
    noise = ["Anywhere life %02d" % i for i in range(40)]
    ranked = rank_typeahead_names(noise + ["Life"], "life")
    assert "Life" in ranked[:_TYPEAHEAD_LIMIT]


# ── get_typeahead applies it, so the classic editor is ranked too ────────────

Base = declarative_base()


class _Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String)


def _session_with(names):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for i, name in enumerate(names, start=1):
        session.add(_Tag(id=i, name=name))
    session.commit()
    return session


@pytest.mark.unit
def test_get_typeahead_returns_exact_match_first():
    """Ranking lives in the shared query, not in one caller, so /get_tags_json
    and the SPA endpoint agree on what the best match is."""
    from cps.db import CalibreDB
    session = _session_with(LIVE_REPRO)
    fake = SimpleNamespace(ensure_session=lambda: None, session=session)
    names = [row["name"] for row in json.loads(CalibreDB.get_typeahead(fake, _Tag, "life"))]
    assert names[0] == "Life"
    assert sorted(names) == sorted(LIVE_REPRO)


@pytest.mark.unit
def test_get_typeahead_still_applies_the_name_replacement():
    from cps.db import CalibreDB
    session = _session_with(["Doe, John|Roe, Jane"])
    fake = SimpleNamespace(ensure_session=lambda: None, session=session)
    names = [row["name"] for row in json.loads(
        CalibreDB.get_typeahead(fake, _Tag, "doe", ("|", ",")))]
    assert names == ["Doe, John,Roe, Jane"]
