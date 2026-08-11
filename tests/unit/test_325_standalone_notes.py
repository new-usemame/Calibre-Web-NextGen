"""A note that is not attached to any highlight (#325).

Neither reader could record a thought that is not tied to a passage. The schema
already permits it -- every anchor column is nullable -- but "no anchor" could not
be *expressed*, because ``annotations.py`` reads ``position_type in (None, "cfi")``
as legacy EPUB CFI. Absence therefore means "resolve me as a CFI", which is the
opposite of what an unanchored note needs.

So unanchored-ness is an explicit, non-NULL ``position_type='unanchored'``. That
makes it a queryable fact rather than a guess, which matters most for the sync
path: a Kobo cannot represent an unanchored note at all (its Bookmark rows need a
NOT NULL container index, and a device note is a *field of* an anchored highlight,
not an entity). Excluding these by predicate is honest; inventing an anchor so the
row "fits" would eventually put a fabricated position on someone's device.

Agreed with the CWNG KOBO session before any of it was built.
"""
import uuid

import pytest

from cps import ub
from cps import annotations as ann


class _FakeBook:
    def __init__(self, bid=1, uuid_="book-uuid"):
        self.id = bid
        self.uuid = uuid_
        self.data = []


class _Session:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def _make(payload, book=None):
    session = _Session()
    row = ann.create_annotation(
        payload,
        user_id=1,
        book=book or _FakeBook(),
        session=session,
        commit=lambda: None,
    )
    return row, session


# --------------------------------------------------------------------------
# The guard that makes the sentinel necessary in the first place.
# --------------------------------------------------------------------------

def test_null_position_type_still_means_legacy_cfi():
    """Regression guard for the reason the sentinel is non-NULL.

    If NULL ever stops meaning "legacy EPUB CFI", then absence becomes available
    and this design should be revisited. Until then, a NULL-typed note would be
    sent down the CFI resolution path, which is why we cannot express "no anchor"
    by leaving the column empty."""
    import inspect

    body = inspect.getsource(ann)
    assert 'position_type", None) in (None, "cfi")' in body, (
        "annotations.py no longer treats NULL position_type as legacy CFI; the "
        "'unanchored' sentinel exists precisely because absence was already taken"
    )


def test_unanchored_is_a_registered_position_type():
    """The column is validated -- an unregistered value raises rather than storing."""
    assert "unanchored" in ub.Annotation._VALID_POSITION_TYPES
    row = ub.Annotation()
    with pytest.raises(ValueError):
        row.position_type = "not-a-real-type"


# --------------------------------------------------------------------------
# Creating one.
# --------------------------------------------------------------------------

def test_a_note_with_no_anchor_is_stored_unanchored():
    row, session = _make({
        "position_type": "unanchored",
        "note_text": "the argument in chapter 3 never lands",
    })
    assert row is session.added[0]
    assert row.position_type == "unanchored"
    assert row.note_text == "the argument in chapter 3 never lands"
    assert row.source == "webreader"
    assert row.annotation_id.startswith(ann.WEBREADER_ID_PREFIX)


def test_an_unanchored_note_asserts_no_position_whatsoever():
    """The whole point: it must not carry a fabricated anchor. The -99 child-index
    sentinel that the CFI-only branch uses is a Kobo-compat lie we must not tell
    here, because a row carrying it looks pushable."""
    row, _ = _make({"position_type": "unanchored", "note_text": "n"})
    for field in (
        "cfi_range",
        "start_container_path",
        "start_container_child_index",
        "start_offset",
        "end_container_path",
        "end_container_child_index",
        "end_offset",
        "highlighted_text",
    ):
        assert getattr(row, field) is None, f"{field} must be NULL on an unanchored note"


def test_an_unanchored_note_carries_no_highlight_colour():
    """There is no highlighted passage to colour. A colour here would render as a
    swatch on a row with nothing to point at."""
    row, _ = _make({"position_type": "unanchored", "note_text": "n", "highlight_color": "blue"})
    assert row.highlight_color is None


def test_chapter_progress_is_kept_for_ordering_but_is_not_an_anchor():
    """Sort order is useful ('roughly here in the book'); an anchor is not, because a
    future push path could take it seriously."""
    row, _ = _make({
        "position_type": "unanchored",
        "note_text": "n",
        "chapter_progress": 0.42,
    })
    assert row.chapter_progress == pytest.approx(0.42)
    assert row.position_type == "unanchored"
    assert row.cfi_range is None


def test_an_empty_note_with_no_anchor_is_still_rejected():
    """Unanchored is not a licence to store nothing at all."""
    with pytest.raises(ValueError):
        _make({"position_type": "unanchored", "note_text": "   "})
    with pytest.raises(ValueError):
        _make({"position_type": "unanchored"})


def test_a_payload_with_no_anchor_and_no_sentinel_still_raises():
    """Unchanged behaviour for everything that is not explicitly unanchored -- a
    highlight that lost its anchor is a bug, not a standalone note."""
    with pytest.raises(ValueError):
        _make({"highlighted_text": "some passage", "highlight_color": "yellow"})


# --------------------------------------------------------------------------
# It must not become a device-push candidate.
#
# There is no push-candidate predicate in the tree yet -- that path is being
# built separately -- so this asserts the *property* any such predicate will key
# on rather than calling a function that does not exist. A Kobo Bookmark row
# needs a NOT NULL container index and the bridge only carries Type='highlight',
# so a row with no container path cannot be represented on a device.
# --------------------------------------------------------------------------

def test_an_unanchored_note_has_nothing_a_device_row_could_be_built_from():
    unanchored, _ = _make({"position_type": "unanchored", "note_text": "n"})
    assert unanchored.start_container_path is None
    assert unanchored.start_container_child_index is None

    # Load-bearing check: an ordinary web highlight DOES carry the container
    # fields, so the assertion above distinguishes the two cases rather than
    # being trivially true of every row.
    anchored, _ = _make({
        "cfi_range": "epubcfi(/6/4!/4/2/2,/1:0,/1:8)",
        "highlighted_text": "passage",
        "highlight_color": "yellow",
    })
    assert anchored.start_container_path == "cfi"
    assert anchored.start_container_child_index == -99


# --------------------------------------------------------------------------
# Over the wire, not just through the function.
#
# `create_annotation` is only half the story: the route also fans the new row
# out to sync targets. A unit test of the function cannot see that, and the fan
# out is exactly where an unanchored row could leak somewhere it does not
# belong -- so these drive the real blueprint with a real Flask client.
# --------------------------------------------------------------------------

import importlib
from types import SimpleNamespace

from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def wired(monkeypatch):
    """The real POST /annotations/<book_id> route, on a real in-memory DB."""
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    session.execute(text("PRAGMA foreign_keys=ON"))
    user = ub.User(name="reader", email="r@e.com", role=0, password="x")
    session.add(user)
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda: session.commit())

    routes = importlib.import_module("cps.annotations")
    book = SimpleNamespace(id=7, uuid="bk-7", title="Book", data=[])
    monkeypatch.setattr(routes, "_resolve_book_or_404", lambda _id: book)
    monkeypatch.setattr(routes, "current_user", user, raising=False)
    monkeypatch.setattr(routes, "user_login_required", lambda f: f, raising=False)
    fanned = []
    monkeypatch.setattr(routes, "_fanout_to_sync_targets",
                        lambda row, bk: fanned.append(row))

    # Register the UNDECORATED view. @user_login_required is applied at import
    # time, so patching the name afterwards does nothing -- and the decorator
    # reads app config we would otherwise have to fabricate. functools.wraps
    # leaves the original on __wrapped__, which is the view body this change
    # actually touches. Auth is deliberately out of scope here: pinning it would
    # be testing Flask-Login, not the annotation route.
    app = Flask(__name__)
    app.add_url_rule('/annotations/<int:book_id>', 'annotations_create',
                     routes.annotations_create.__wrapped__, methods=['POST'])
    return app.test_client(), session, fanned


def test_posting_an_unanchored_note_is_accepted_over_the_wire(wired):
    client, session, _ = wired
    res = client.post('/annotations/7', json={
        'position_type': 'unanchored',
        'note_text': 'the argument in chapter 3 never lands',
    })
    assert res.status_code == 201, res.get_data(as_text=True)
    body = res.get_json()
    assert body['note_text'] == 'the argument in chapter 3 never lands'
    assert body['position_type'] == 'unanchored'
    assert body['cfi_range'] is None
    assert body['highlighted_text'] is None

    row = session.query(ub.Annotation).one()
    assert row.position_type == 'unanchored'
    assert row.start_container_path is None


def test_the_route_still_rejects_a_highlight_that_lost_its_anchor(wired):
    """Unchanged behaviour for everything that is not explicitly unanchored --
    a 400, not a silently-created standalone note."""
    client, session, _ = wired
    res = client.post('/annotations/7', json={
        'highlighted_text': 'some passage', 'highlight_color': 'yellow',
    })
    assert res.status_code == 400
    assert res.get_json()['error'] == 'bad_anchor'
    assert session.query(ub.Annotation).count() == 0


def test_an_unanchored_note_is_still_fanned_out_to_sync_targets(wired):
    """It SHOULD reach Hardcover: a journal entry carrying a note and no quote
    is precisely what a standalone note is. This pins that we have not
    accidentally excluded it -- the exclusion that matters is the device path,
    which cannot represent it, not the reading-tracker path, which can."""
    client, _, fanned = wired
    client.post('/annotations/7', json={
        'position_type': 'unanchored', 'note_text': 'a thought',
    })
    assert len(fanned) == 1
    assert fanned[0].position_type == 'unanchored'


def test_hardcover_does_not_reject_a_note_with_no_highlighted_text():
    """The handler skips rows with no text content. An unanchored note has no
    highlighted_text, so this pins that note_text alone keeps it eligible --
    otherwise every standalone note would silently fail to sync."""
    from cps.services.annotation_sync.hardcover import HardcoverHandler
    import inspect

    src = inspect.getsource(HardcoverHandler.push)
    assert 'if not annotation.highlighted_text and not annotation.note_text:' in src, (
        "Hardcover's empty-content guard changed shape; an unanchored note "
        "(no highlighted_text, note_text set) must still pass it"
    )
