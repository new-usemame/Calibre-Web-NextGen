# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for fork issue #627 — marking a book *unread* must clear the
"Started reading" and "Last synced" dates, not just the percentage.

Reporter @uschi1 confirmed the v4.1.21 "Send to Reader" fix worked, then
reported the residual: *"when I set my test book to 'unread', it doesn't delete
the start date and the last sync date"*.

Root cause: ``helper.reset_reading_position`` nulled only
``KoboBookmark.progress_percent`` / ``content_source_progress_percent``. The
same row's ``created_at`` (rendered as "Started reading") and its
``location_*`` resume point survived the reset. ``last_modified`` (rendered as
"Last synced") carries an ``onupdate`` default, so the very act of nulling the
percentage *bumped* it to the moment of the reset — the user marked a book
unread and watched "Last synced" change to now.

Two independent defects, so two independent fixes, both pinned here:

  * the reset is now a genuine reset — ``created_at``, the ``location_*``
    resume point (which ``kobo.get_current_bookmark_response`` serialises back
    to the device as ``Location``, letting it restore the exact position) and
    the ``KoboStatistics`` counters are cleared;
  * the three display values are resolved as ONE unit by
    ``helper.get_kosync_progress_display`` — with no position, the timestamps
    describe nothing and are reported as ``None``. This is what repairs rows
    that were already half-reset by an older build, without a migration.

``last_modified`` is deliberately NOT cleared: ``reset_reading_position``
depends on its ``onupdate`` bump so the reset propagates to the device as the
newest reading state on the next sync. Pinned below so a future edit can't
"tidy" it away.
"""
import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub, helper


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_synced_book(session, user_id, book_id, percent=42.5):
    """A book that has genuinely been read on a device: a percentage, a start
    date, a resume location and accumulated statistics."""
    from datetime import datetime, timezone

    started = datetime(2026, 7, 1, 9, 0, 0)
    krs = ub.KoboReadingState(user_id=user_id, book_id=book_id)
    krs.current_bookmark = ub.KoboBookmark(
        progress_percent=percent,
        content_source_progress_percent=percent,
        created_at=started,
        last_modified=datetime(2026, 7, 20, 18, 30, 0),
        location_source="/OEBPS/ch05.xhtml",
        location_type="KoboSpan",
        location_value="kobo.5.1",
    )
    krs.statistics = ub.KoboStatistics(
        spent_reading_minutes=212, remaining_time_minutes=48)
    session.add(krs)
    return krs


# --- the reset must be a genuine reset ------------------------------------

@pytest.mark.unit
def test_reset_clears_started_reading_date(session):
    """The reporter's first symptom: the start date survived the reset."""
    _make_synced_book(session, 1, 42)
    session.commit()

    helper.reset_reading_position(session, 1, 42)
    session.commit()

    krs = session.query(ub.KoboReadingState).filter_by(user_id=1, book_id=42).first()
    assert krs.current_bookmark.created_at is None


@pytest.mark.unit
def test_reset_clears_device_resume_location(session):
    """``location_value`` is serialised back to the device as ``Location``.
    Left in place, a Kobo restores the exact position the user just cleared."""
    _make_synced_book(session, 1, 42)
    session.commit()

    helper.reset_reading_position(session, 1, 42)
    session.commit()

    bm = session.query(ub.KoboReadingState).filter_by(user_id=1, book_id=42).first().current_bookmark
    assert bm.location_value is None
    assert bm.location_type is None
    assert bm.location_source is None


@pytest.mark.unit
def test_reset_clears_reading_statistics(session):
    """"Unread" should not keep reporting 212 minutes spent reading."""
    _make_synced_book(session, 1, 42)
    session.commit()

    helper.reset_reading_position(session, 1, 42)
    session.commit()

    stats = session.query(ub.KoboReadingState).filter_by(user_id=1, book_id=42).first().statistics
    assert stats.spent_reading_minutes is None
    assert stats.remaining_time_minutes is None


@pytest.mark.unit
def test_reset_keeps_last_modified_set_for_device_propagation(session):
    """``last_modified`` must stay populated — the reset propagates to the
    device by being the newest reading state on the next sync."""
    _make_synced_book(session, 1, 42)
    session.commit()

    helper.reset_reading_position(session, 1, 42)
    session.commit()

    bm = session.query(ub.KoboReadingState).filter_by(user_id=1, book_id=42).first().current_bookmark
    assert bm.last_modified is not None


@pytest.mark.unit
def test_reset_is_scoped_to_one_user_and_book(session):
    """Another user's copy, and this user's other book, are untouched."""
    _make_synced_book(session, 1, 42)
    _make_synced_book(session, 2, 42)
    _make_synced_book(session, 1, 99)
    session.commit()

    helper.reset_reading_position(session, 1, 42)
    session.commit()

    other_user = session.query(ub.KoboReadingState).filter_by(user_id=2, book_id=42).first()
    other_book = session.query(ub.KoboReadingState).filter_by(user_id=1, book_id=99).first()
    assert other_user.current_bookmark.created_at is not None
    assert other_user.current_bookmark.location_value == "kobo.5.1"
    assert other_book.current_bookmark.created_at is not None
    assert other_book.statistics.spent_reading_minutes == 212


# --- the three chips are one unit -----------------------------------------

@pytest.mark.unit
def test_display_hides_dates_when_no_progress(session):
    """A row left half-reset by an older build still has ``created_at`` and a
    freshly-bumped ``last_modified``. With no position those describe nothing,
    so the display resolver reports all three as None — this is what repairs
    existing installs without a migration."""
    from datetime import datetime

    krs = ub.KoboReadingState(user_id=1, book_id=42)
    krs.current_bookmark = ub.KoboBookmark(
        progress_percent=None,
        content_source_progress_percent=None,
        created_at=datetime(2026, 7, 1, 9, 0, 0),
        last_modified=datetime(2026, 7, 26, 7, 39, 0),
    )
    krs.statistics = ub.KoboStatistics()
    session.add(krs)
    session.commit()

    progress, last_synced, started = helper.get_kosync_progress_display(session, 1, 42)
    assert progress is None
    assert last_synced is None
    assert started is None


@pytest.mark.unit
def test_display_returns_all_three_when_progress_present(session):
    _make_synced_book(session, 1, 42, percent=42.5)
    session.commit()

    progress, last_synced, started = helper.get_kosync_progress_display(session, 1, 42)
    assert progress == 42.5
    assert last_synced is not None
    assert started is not None


@pytest.mark.unit
def test_display_handles_book_never_synced(session):
    """No KoboReadingState row at all must not raise."""
    assert helper.get_kosync_progress_display(session, 1, 4242) == (None, None, None)


@pytest.mark.unit
def test_display_handles_state_without_bookmark(session):
    """A reading state with no bookmark row must not raise."""
    session.add(ub.KoboReadingState(user_id=1, book_id=42))
    session.commit()

    assert helper.get_kosync_progress_display(session, 1, 42) == (None, None, None)


# --- both surfaces must share the resolver --------------------------------

@pytest.mark.unit
def test_both_book_surfaces_use_the_shared_resolver():
    """The classic detail page and the SPA book API each used to inline the
    same query, so a fix to one silently missed the other. Pin that both now
    route through the shared helper."""
    from cps import web
    from cps.api import books as api_books

    for module in (web, api_books):
        source = inspect.getsource(module)
        assert "get_kosync_progress_display" in source, (
            f"{module.__name__} no longer uses the shared kosync display "
            "resolver; the two book surfaces will drift apart again"
        )


# --- the user-visible chips ------------------------------------------------

def _detail_progress_region():
    """The three reading-progress chips, lifted verbatim from the shipped
    template so this renders the real source rather than a copy of it."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    html = (root / "cps" / "templates" / "detail.html").read_text()
    start = html.index("{% if kosync_progress is not none %}")
    end = html.index("{% endif %}", html.index("kosync_progress_timestamp")) + len("{% endif %}")
    return html[start:end]


def _render_progress_region(**context):
    import jinja2

    env = jinja2.Environment(autoescape=True)
    env.filters["formatdate"] = lambda value: value.strftime("%Y-%m-%d")
    env.filters["formatfloat"] = lambda value, places=1: f"{value:.{places}f}"
    env.globals["_"] = lambda s: s
    return env.from_string(_detail_progress_region()).render(**context)


@pytest.mark.unit
def test_detail_page_shows_no_reading_dates_after_marking_unread():
    """The reporter's symptom, at the surface they actually looked at: after
    the reset the detail page must render neither date."""
    html = _render_progress_region(
        kosync_progress=None,
        kosync_progress_created_at=None,
        kosync_progress_timestamp=None,
    )
    assert "Started reading" not in html
    assert "Last synced" not in html
    assert "KOReader Progress" not in html


@pytest.mark.unit
def test_detail_page_still_shows_reading_dates_for_a_synced_book():
    """The chips must survive for a book that genuinely has a position —
    the fix must not simply delete the feature."""
    from datetime import datetime

    html = _render_progress_region(
        kosync_progress=42.5,
        kosync_progress_created_at=datetime(2026, 7, 1, 9, 0, 0),
        kosync_progress_timestamp=datetime(2026, 7, 20, 18, 30, 0),
    )
    assert "Started reading" in html
    assert "2026-07-01" in html
    assert "Last synced" in html
    assert "2026-07-20" in html
    assert "42.5%" in html


@pytest.mark.unit
def test_reset_still_called_when_marking_unread():
    """Guard the #683 wiring the reporter's fix depends on."""
    source = inspect.getsource(helper.edit_book_read_status)
    assert source.count("reset_reading_position") >= 2, (
        "edit_book_read_status must reset the position in both read-status "
        "backends (built-in ReadBook and the custom read column)"
    )
