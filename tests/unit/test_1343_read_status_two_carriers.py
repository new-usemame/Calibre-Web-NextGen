# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1343 — read state must not live in two carriers that can disagree.

#1340 stopped the web reader from un-finishing a book the user marked Read, by
refusing to share a position when ``ub.ReadBook.read_status`` is FINISHED. On a
default install that is right. On a ``config_read_column`` install it was wrong
in *both* directions, because ``helper.edit_book_read_status``'s custom-column
branch wrote only the Calibre column and never mirrored the state into
``ub.ReadBook`` — while ``update_book_read_status`` wrote ``ub.ReadBook``
unconditionally. The two carriers drifted, and each direction produced a bug:

  1. **Permanent silent wedge** (a regression #1340 introduced). Finish a book
     in the browser -> ``ReadBook.read_status`` = FINISHED. Click "mark unread"
     to re-read it -> the custom branch clears the Calibre column, and
     ``reset_reading_position`` folds only IN_PROGRESS back to UNREAD, so
     ``ReadBook`` stays FINISHED. Every later web-reader save is refused,
     forever, with no web-UI path to clear it. Before #1340 the write went
     through and incidentally healed the stale FINISHED — the guard removed
     that silent repair without adding one.

  2. **The original bug survived** for the same users. "Mark read" wrote only
     the Calibre column, so no FINISHED row existed and the guard never fired:
     opening the book still counted a reading session and pushed
     ``StatusInfo: "Reading"`` to the Kobo.

  3. **A finished book froze below 100%.** ``update_book_read_status`` finishes
     at >= 99%, so the save that crossed the line marked the book FINISHED and
     the guard then blocked the *next* one — the stored percentage stuck at
     e.g. 99.2 and the device was told 99% indefinitely.

Fixed by closing the root cause (the custom branch mirrors read state into
``ub.ReadBook`` in both directions, restoring the escape hatch and giving the
subsystem one source of truth) and by narrowing the guard to refuse only writes
that would actually DOWNGRADE a finished book, rather than every write.

The percentage->status thresholds now have a single implementation
(``kosync.read_status_for_percentage``) that both the writer and the guard call,
so the two cannot drift into a third disagreement.
"""

import inspect
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
import cps.progress_syncing.models  # noqa: F401 — registers KOSyncProgress on ub.Base


# ── harness ──────────────────────────────────────────────────────────────────

def _session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _kosync():
    """The kosync *module* — ``protocols/__init__`` re-exports the Blueprint
    under the same attribute name and shadows the submodule."""
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


def _seed(session, user_id, book_id, status, percent=None):
    """Build the ReadBook -> KoboReadingState -> KoboBookmark graph."""
    read = ub.ReadBook(user_id=user_id, book_id=book_id, read_status=status)
    state = ub.KoboReadingState(user_id=user_id, book_id=book_id)
    state.current_bookmark = ub.KoboBookmark(progress_percent=percent)
    state.statistics = ub.KoboStatistics()
    read.kobo_reading_state = state
    session.add(read)
    session.commit()
    return read


def _read_status(session, user_id, book_id):
    row = (session.query(ub.ReadBook)
           .filter(ub.ReadBook.user_id == user_id,
                   ub.ReadBook.book_id == book_id).first())
    return None if row is None else row.read_status


def _stored_percent(session, user_id, book_id):
    state = (session.query(ub.KoboReadingState)
             .filter(ub.KoboReadingState.user_id == user_id,
                     ub.KoboReadingState.book_id == book_id).first())
    if state is None or state.current_bookmark is None:
        return None
    return state.current_bookmark.progress_percent


def _service(monkeypatch, session, read_column=0):
    """The reading-position service bound to an in-memory session.

    ``read_column`` defaults to 0 only where the test genuinely means "default
    install" — #1343 exists because the whole suite used to hard-pin it there.
    """
    from cps.services import reading_position as mod
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(_kosync().config, "config_read_column", read_column,
                        raising=False)
    if read_column:
        # The kosync path mirrors FINISHED into the Calibre column; that write
        # needs a real metadata.db, and it is not what these tests measure.
        monkeypatch.setattr(_kosync(), "_mark_custom_read_column",
                            lambda book_id: None)
    return mod


# ── A. one implementation of the percentage -> status thresholds ─────────────

class TestThresholdSingleSourceOfTruth:
    """The guard has to ask "would this sample finish the book?". Answering it
    with a second copy of ``99.0`` is how carriers drift in the first place."""

    @pytest.mark.unit
    @pytest.mark.parametrize("percent,expected_name", [
        (0.0, "STATUS_UNREAD"),
        (0.5, "STATUS_IN_PROGRESS"),
        (55.0, "STATUS_IN_PROGRESS"),
        (98.9, "STATUS_IN_PROGRESS"),
        (99.0, "STATUS_FINISHED"),
        (99.2, "STATUS_FINISHED"),
        (100.0, "STATUS_FINISHED"),
    ])
    def test_maps_percentage_to_status(self, percent, expected_name):
        fn = getattr(_kosync(), "read_status_for_percentage", None)
        assert fn is not None, (
            "kosync must expose read_status_for_percentage as the single "
            "implementation of the percentage->status thresholds (#1343)"
        )
        assert fn(percent) == getattr(ub.ReadBook, expected_name)

    @pytest.mark.unit
    def test_writer_uses_the_shared_helper_not_its_own_literal(self):
        """``update_book_read_status`` must not keep a private copy of 99.0."""
        src = inspect.getsource(_kosync().update_book_read_status)
        assert "read_status_for_percentage" in src, (
            "update_book_read_status must derive the status from the shared "
            "helper so the guard in reading_position cannot drift from it"
        )
        assert "99.0" not in src, (
            "the finished threshold must live in exactly one place (#1343); "
            "a second literal here is what lets the two carriers disagree"
        )

    @pytest.mark.unit
    def test_guard_uses_the_shared_helper(self):
        from cps.services import reading_position
        src = inspect.getsource(reading_position.record_web_reader_progress)
        assert "read_status_for_percentage" in src, (
            "the finished-book guard must ask the shared helper whether the "
            "incoming sample would downgrade the book, not block every write"
        )


# ── B. the guard refuses downgrades, not every write ────────────────────────

class TestFinishedBookGuardOnlyBlocksDowngrades:

    @pytest.mark.unit
    def test_reopening_a_finished_book_is_still_refused(self, monkeypatch):
        """#1340's actual protection — the sample that means "I just opened
        this" must never un-finish a book the user marked Read."""
        session = _session()
        mod = _service(monkeypatch, session)
        _seed(session, 7, 42, ub.ReadBook.STATUS_FINISHED, percent=None)

        assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 5.0) is False
        assert _read_status(session, 7, 42) == ub.ReadBook.STATUS_FINISHED
        assert _stored_percent(session, 7, 42) is None

    @pytest.mark.unit
    def test_finishing_in_the_browser_reaches_100_percent(self, monkeypatch):
        """#1343 item 3: crossing 99% marked the book FINISHED, and the guard
        then blocked the save that would have reached 100 — so the detail page
        and the device showed 99% forever."""
        session = _session()
        mod = _service(monkeypatch, session)
        _seed(session, 7, 42, ub.ReadBook.STATUS_FINISHED, percent=99.2)

        assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 100.0) is True
        assert _stored_percent(session, 7, 42) == 100.0
        assert _read_status(session, 7, 42) == ub.ReadBook.STATUS_FINISHED

    @pytest.mark.unit
    def test_a_finished_sample_on_a_bare_finished_row_is_accepted(self, monkeypatch):
        """The bare-KoboBookmark graph "mark as read" leaves behind (NULL
        percent) must still be fillable by a genuine 100% read-through."""
        session = _session()
        mod = _service(monkeypatch, session)
        _seed(session, 7, 42, ub.ReadBook.STATUS_FINISHED, percent=None)

        assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 99.5) is True
        assert _stored_percent(session, 7, 42) == 99.5

    @pytest.mark.unit
    def test_furthest_wins_still_applies_to_a_finished_book(self, monkeypatch):
        """Letting finished-level samples through must not defeat furthest-wins:
        a lower one is still refused even though it is >= the threshold."""
        session = _session()
        mod = _service(monkeypatch, session)
        _seed(session, 7, 42, ub.ReadBook.STATUS_FINISHED, percent=100.0)

        assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 99.1) is False
        assert _stored_percent(session, 7, 42) == 100.0

    @pytest.mark.unit
    def test_reading_session_count_is_not_incremented_by_a_finished_sample(self, monkeypatch):
        """Accepting the 100% save must not look like "started reading again"."""
        session = _session()
        mod = _service(monkeypatch, session)
        row = _seed(session, 7, 42, ub.ReadBook.STATUS_FINISHED, percent=99.2)
        row.times_started_reading = 1
        session.commit()

        assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 100.0) is True
        assert (session.query(ub.ReadBook)
                .filter(ub.ReadBook.user_id == 7,
                        ub.ReadBook.book_id == 42).first().times_started_reading) == 1


# ── C. the mirror itself ────────────────────────────────────────────────────

class TestMirrorReadStatusToReadBook:
    """The custom-column branch's missing half: whatever the Calibre column now
    says, ``ub.ReadBook.read_status`` must say the same."""

    def _mirror(self):
        from cps import helper
        fn = getattr(helper, "mirror_read_status_to_readbook", None)
        assert fn is not None, (
            "helper must expose mirror_read_status_to_readbook so a custom "
            "read-column toggle reaches ub.ReadBook too (#1343)"
        )
        return fn

    @pytest.mark.unit
    def test_marking_read_creates_a_finished_row_when_none_exists(self):
        session = _session()
        self._mirror()(session, 7, 42, True)
        session.commit()
        assert _read_status(session, 7, 42) == ub.ReadBook.STATUS_FINISHED

    @pytest.mark.unit
    def test_marking_read_updates_an_existing_row(self):
        session = _session()
        _seed(session, 7, 42, ub.ReadBook.STATUS_IN_PROGRESS, percent=30.0)
        self._mirror()(session, 7, 42, True)
        session.commit()
        assert _read_status(session, 7, 42) == ub.ReadBook.STATUS_FINISHED

    @pytest.mark.unit
    def test_marking_unread_clears_a_finished_row(self):
        """THE escape hatch. Without this the #1340 guard is permanent."""
        session = _session()
        _seed(session, 7, 42, ub.ReadBook.STATUS_FINISHED, percent=100.0)
        self._mirror()(session, 7, 42, False)
        session.commit()
        assert _read_status(session, 7, 42) == ub.ReadBook.STATUS_UNREAD

    @pytest.mark.unit
    def test_marking_unread_does_not_create_a_row(self):
        """Nothing to clear means nothing to write — an UNREAD row is the
        default state and inventing one is just a row per never-read book."""
        session = _session()
        self._mirror()(session, 7, 42, False)
        session.commit()
        assert _read_status(session, 7, 42) is None

    @pytest.mark.unit
    def test_scoped_to_one_user_and_one_book(self):
        session = _session()
        _seed(session, 7, 42, ub.ReadBook.STATUS_FINISHED, percent=100.0)
        _seed(session, 8, 42, ub.ReadBook.STATUS_FINISHED, percent=100.0)
        _seed(session, 7, 43, ub.ReadBook.STATUS_FINISHED, percent=100.0)

        self._mirror()(session, 7, 42, False)
        session.commit()

        assert _read_status(session, 7, 42) == ub.ReadBook.STATUS_UNREAD
        assert _read_status(session, 8, 42) == ub.ReadBook.STATUS_FINISHED
        assert _read_status(session, 7, 43) == ub.ReadBook.STATUS_FINISHED


# ── D. the round trip through the real toggle, on a custom-column install ────

@pytest.fixture
def custom_column_install(monkeypatch):
    """Drive the real ``helper.edit_book_read_status`` custom-column branch
    against an in-memory app.db, with Calibre's side faked.

    The Calibre write needs reflected ``cc_classes`` and a metadata.db, which a
    unit test has no business standing up; what #1343 is about is the app.db
    carrier the branch forgot, so that half is real.
    """
    from cps import helper

    session = _session()
    column = SimpleNamespace(value=False)
    book = SimpleNamespace()
    setattr(book, "custom_column_5", [column])

    calibre_db = SimpleNamespace(
        get_filtered_book=lambda book_id, allow_show_archived=False: book,
        session=SimpleNamespace(commit=lambda: None, add=lambda obj: None,
                                rollback=lambda: None),
    )

    monkeypatch.setattr(helper, "calibre_db", calibre_db)
    monkeypatch.setattr(helper.config, "config_read_column", 5, raising=False)
    monkeypatch.setattr(helper, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(helper.ub, "session", session)
    monkeypatch.setattr(helper.ub, "session_commit", lambda *a, **k: True)
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(_kosync().config, "config_read_column", 5, raising=False)
    monkeypatch.setattr(_kosync(), "_mark_custom_read_column", lambda book_id: None)

    return SimpleNamespace(session=session, column=column, helper=helper)


class TestCustomColumnRoundTrip:

    @pytest.mark.unit
    def test_marking_read_protects_the_book_from_being_reopened(self, custom_column_install):
        """#1343 item 2 — the original #1316 symptom, still live for these
        users because no FINISHED row existed for the guard to see."""
        env = custom_column_install
        from cps.services import reading_position

        assert env.helper.edit_book_read_status(42, True) == ""
        assert env.column.value is True
        assert _read_status(env.session, 7, 42) == ub.ReadBook.STATUS_FINISHED

        # Opening the book in the web reader must not un-finish it.
        assert reading_position.record_web_reader_progress(
            SimpleNamespace(id=7), 42, 5.0) is False
        assert _read_status(env.session, 7, 42) == ub.ReadBook.STATUS_FINISHED

    @pytest.mark.unit
    def test_marking_unread_unwedges_the_reader(self, custom_column_install):
        """#1343 item 1 — the regression. After "mark unread" the user must be
        able to read the book again; before the fix every save was refused
        forever because ReadBook stayed FINISHED."""
        env = custom_column_install
        from cps.services import reading_position

        # The browser finished it: update_book_read_status wrote ReadBook
        # regardless of the custom column.
        _seed(env.session, 7, 42, ub.ReadBook.STATUS_FINISHED, percent=100.0)
        env.column.value = True

        assert env.helper.edit_book_read_status(42, False) == ""
        assert env.column.value is False
        assert _read_status(env.session, 7, 42) == ub.ReadBook.STATUS_UNREAD

        assert reading_position.record_web_reader_progress(
            SimpleNamespace(id=7), 42, 25.0) is True
        assert _stored_percent(env.session, 7, 42) == 25.0

    @pytest.mark.unit
    def test_marking_unread_with_no_column_row_does_not_mark_it_read(
            self, custom_column_install, monkeypatch):
        """``value=read_status or 1`` wrote 1 for an explicit False, so asking
        to un-read a book that had no column row yet marked it Read instead —
        and would now push that inversion straight into ub.ReadBook, wedging
        the reader for a user who asked for the opposite."""
        env = custom_column_install
        from cps import helper

        created = []
        book = SimpleNamespace()
        setattr(book, "custom_column_5", [])          # no row for this book yet
        monkeypatch.setattr(env.helper.calibre_db, "get_filtered_book",
                            lambda book_id, allow_show_archived=False: book)
        monkeypatch.setattr(env.helper.calibre_db.session, "add", created.append)
        monkeypatch.setattr(helper.db, "cc_classes",
                            {5: lambda value, book: SimpleNamespace(value=value, book=book)},
                            raising=False)

        assert env.helper.edit_book_read_status(42, False) == ""

        assert len(created) == 1
        assert created[0].value is False, (
            "an explicit 'mark unread' must not write a READ value"
        )
        assert _read_status(env.session, 7, 42) != ub.ReadBook.STATUS_FINISHED

    @pytest.mark.unit
    def test_toggle_without_an_explicit_value_mirrors_both_ways(self, custom_column_install):
        """The bare ``/ajax/toggleread`` path passes no read_status at all."""
        env = custom_column_install

        assert env.helper.edit_book_read_status(42) == ""
        assert env.column.value is True
        assert _read_status(env.session, 7, 42) == ub.ReadBook.STATUS_FINISHED

        assert env.helper.edit_book_read_status(42) == ""
        assert env.column.value is False
        assert _read_status(env.session, 7, 42) == ub.ReadBook.STATUS_UNREAD


# ── E. the default install must be untouched ────────────────────────────────

class TestDefaultInstallUnchanged:

    @pytest.mark.unit
    def test_mirror_is_gated_on_config_read_column(self):
        """The non-custom branch already writes ``ub.ReadBook`` directly; a
        second write there would be a redundant path that can drift.

        Both *directions* of the mirror are pinned behaviourally by
        ``TestCustomColumnRoundTrip`` — deliberately not by a call-site count
        here, which would forbid the single ``mirror(..., not now_unread)`` call
        that already covers both.
        """
        from cps import helper
        src = inspect.getsource(helper.edit_book_read_status)
        head, sep, tail = src.partition("\n    else:")
        assert sep, "expected the config_read_column branch split to still exist"
        assert "mirror_read_status_to_readbook" not in head, (
            "the default-install branch already owns ub.ReadBook.read_status; "
            "the mirror belongs only to the custom-column branch"
        )
        assert "mirror_read_status_to_readbook" in tail, (
            "the custom-column branch must mirror the toggle into ub.ReadBook "
            "or the #1340 guard has no way to fire and no way to clear (#1343)"
        )

    @pytest.mark.unit
    def test_default_install_guard_and_writes_are_unaffected(self, monkeypatch):
        session = _session()
        mod = _service(monkeypatch, session, read_column=0)
        _seed(session, 7, 42, ub.ReadBook.STATUS_IN_PROGRESS, percent=20.0)

        assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 55.0) is True
        assert _stored_percent(session, 7, 42) == 55.0
        assert _read_status(session, 7, 42) == ub.ReadBook.STATUS_IN_PROGRESS
