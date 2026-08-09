# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1445 — withholding a percentage-only position must not be silent.

#1366 gated percentage-only rows behind ``?position_kinds=`` on the GET, and
that gate is right: an already-installed plugin handed one of those rows feeds
the sentinel to ``GotoXPointer`` and stores an unrecoverable position.  So a
client that has not advertised support is served nothing.

The cost is that "nothing" is spelled the same way as "this book has never been
synced": an empty body.  A third-party client author sees an empty response,
has no reason to suspect a position exists, and nothing anywhere names the
parameter that would reveal it.  @sroebert hit exactly that with Crossink and
had to read our source to find ``position_kinds`` before web-to-KOReader sync
worked.

What is pinned here:
  * a row withheld *only* because the client stayed silent says so, and names
    the kind it is withholding, so the response itself is the documentation;
  * a book with genuinely no progress stays bare — the hint has to mean
    "there is something here", or it is noise;
  * the withheld response still carries no position of any kind, so an
    already-installed plugin resolves it to "no progress" exactly as before;
  * the withholding is logged at WARNING naming the parameter, matching the
    ``sync_disabled`` precedent that exists so an admin can diagnose a dead
    sync from one log line (#312);
  * a client that *did* advertise is unaffected and gets no hint;
  * the parameter is documented in the protocol README, not only in source.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps.progress_syncing.models import AppBase

BOOK_ID = 42
USER_ID = 1

README = Path(__file__).resolve().parents[2] / "cps" / "progress_syncing" / "README.md"


def _kosync_module():
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def protocol(monkeypatch):
    """A real kosync HTTP surface over a real in-memory progress table."""
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    monkeypatch.setattr(module, "ub", MagicMock(session=session))
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: SimpleNamespace(id=USER_ID))
    monkeypatch.setattr(module, "update_book_read_status", lambda *_a: None)
    monkeypatch.setattr(module, "push_reading_state_to_hardcover", lambda *_a: None)
    monkeypatch.setattr(module, "get_book_checksums",
                        lambda book_id: ["digest-a"] if book_id else [])
    monkeypatch.setattr(module, "enrich_response_with_book_info",
                        lambda response, document: (response, BOOK_ID, "EPUB", "Fixture", "koreader"))

    app = Flask(__name__)
    app.register_blueprint(module.kosync)
    yield module, app.test_client(), session
    session.close()


def _web_reader_saves(module, percent=45.67):
    """The producer that can only express a percentage (the browser, or a Kobo)."""
    module.record_percentage_only_progress(USER_ID, BOOK_ID, percent, device="Web reader")
    module.ub.session.commit()


def _pull(client, advertises_percentage):
    """GET as a new plugin (advertises) or an already-installed one (does not)."""
    url = "/kosync/syncs/progress/digest-a"
    if advertises_percentage:
        url += "?position_kinds=locator,percentage"
    return client.get(url)


# ── the fix ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_withheld_row_names_the_kind_it_is_withholding(protocol):
    """The reported symptom: an empty body that should have said why.

    Fails before the fix — the response is ``{}``, indistinguishable from a book
    that has never been synced, so nothing tells the client a position exists.
    """
    module, client, _ = protocol

    _web_reader_saves(module)

    body = _pull(client, advertises_percentage=False).get_json()
    assert body.get("position_kinds_available") == ["percentage"], (
        "a position was withheld purely because the client stayed silent; "
        "the response has to say so and name the kind to ask for"
    )


@pytest.mark.unit
def test_a_book_with_no_progress_at_all_stays_bare(protocol):
    """The hint must mean 'there is something here', not appear unconditionally.

    An unconditional hint would train a client to send ``position_kinds`` and
    still find nothing, which is the same dead end wearing a different hat.
    """
    _module, client, _ = protocol

    body = _pull(client, advertises_percentage=False).get_json()
    assert "position_kinds_available" not in body, (
        "no row exists, so there is nothing being withheld to advertise"
    )


@pytest.mark.unit
def test_withheld_response_still_carries_no_position(protocol):
    """Compatibility pin: the hint must not become a position by accident.

    An already-installed plugin resolves this body with ``resolveRemotePosition``.
    Any of ``progress``/``percentage``/``position_kind`` appearing here would
    reach ``GotoXPointer`` or ``GotoPercent`` on a client that never advertised
    it could act on one — the exact outcome the gate exists to prevent.
    """
    module, client, _ = protocol

    _web_reader_saves(module)

    body = _pull(client, advertises_percentage=False).get_json()
    for forbidden in ("progress", "percentage", "position_kind", "timestamp"):
        assert forbidden not in body, (
            f"{forbidden!r} must not reach a client that cannot act on it"
        )


@pytest.mark.unit
def test_withholding_is_logged_with_the_parameter_name(protocol, monkeypatch):
    """An admin debugging a dead sync gets the answer from one log line (#312)."""
    module, client, _ = protocol

    logger = MagicMock()
    monkeypatch.setattr(module, "log", logger)

    _web_reader_saves(module)
    _pull(client, advertises_percentage=False)

    warnings = " ".join(str(call) for call in logger.warning.call_args_list)
    assert "position_kinds" in warnings, (
        "the withholding is invisible from the server side too; "
        f"warnings emitted: {logger.warning.call_args_list}"
    )


@pytest.mark.unit
def test_a_client_that_advertised_is_unaffected(protocol):
    """Regression pin: the hint is for the silent client only."""
    module, client, _ = protocol

    _web_reader_saves(module)

    body = _pull(client, advertises_percentage=True).get_json()
    assert body["position_kind"] == "percentage"
    assert body["progress"] is None
    assert body["percentage"] == pytest.approx(0.4567)
    assert "position_kinds_available" not in body, (
        "nothing is being withheld from this client, so there is nothing to advertise"
    )


@pytest.mark.unit
def test_a_locator_row_is_never_treated_as_withheld(protocol):
    """A real locator is served to everyone, so no hint applies to it."""
    _module, client, _ = protocol

    client.put("/kosync/syncs/progress", json={
        "document": "digest-a",
        "progress": "/body/DocFragment[12]/body/div/p[3].0",
        "percentage": 0.30,
        "device": "Crosspoint",
        "device_id": "Crosspoint",
    })

    body = _pull(client, advertises_percentage=False).get_json()
    assert body["position_kind"] == "locator"
    assert "position_kinds_available" not in body


# ── the documentation half of #1445 ──────────────────────────────────────────

@pytest.mark.unit
def test_position_kinds_is_documented_in_the_protocol_readme():
    """The ask: a third-party client can implement this without reading source.

    ``cps/progress_syncing/README.md`` is the only protocol reference we ship.
    The parameter, its accepted values and its default all have to be findable
    there, because a client author has no reason to grep our Python.
    """
    text = README.read_text(encoding="utf-8")

    assert "position_kinds" in text, "the parameter is not named in the protocol docs"
    for value in ("locator", "percentage"):
        assert re.search(rf"\b{value}\b", text), f"accepted value {value!r} is undocumented"
    assert "position_kind" in text, "the response field is undocumented"


@pytest.mark.unit
def test_documented_parameter_names_match_the_implementation():
    """Source-pin: the docs cannot drift from the constants they describe.

    Nothing at runtime would report a divergence — the docs would simply be
    wrong, and wrong docs on a protocol surface are worse than none.
    """
    module = _kosync_module()
    text = README.read_text(encoding="utf-8")

    assert module.POSITION_KINDS_PARAM in text
    assert module.POSITION_KIND_LOCATOR in text
    assert module.POSITION_KIND_PERCENTAGE in text
