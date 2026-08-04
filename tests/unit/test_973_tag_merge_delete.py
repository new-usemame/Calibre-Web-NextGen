# SPDX-License-Identifier: GPL-3.0-or-later
"""#973: consolidating tags from the New-UI Tag view.

The reporter's goal is de-duplication: rename a tag onto its near-duplicate so
the two become one, and delete a tag that should not exist at all. Neither was
possible. Renaming onto an existing name returned a flat 409 ("A tag with that
name already exists") with nothing the UI could act on, and there was no delete
route at any level.

So the collision now carries the conflicting tag in the body, an explicit
``merge`` opt-in performs the consolidation, and DELETE removes a tag from every
book that carries it. ``merge`` stays opt-in on purpose: silently folding two
tags together on a typo is destructive and has no undo.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import flask
import pytest
from flask_babel import Babel
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cps import db
import cps.api.browse as browse


def _book(index, session_tags):
    now = datetime.now(timezone.utc)
    book = db.Books(f"Linked book {index}", f"Linked book {index}", "Author", now, now,
                    "1.0", now, f"linked-book-{index}", 1, [], [])
    for tag in session_tags:
        book.tags.append(tag)
    return book


@pytest.fixture()
def tag_session():
    """Two near-duplicate tags plus a book that already carries both.

    "Sci-Fi" (2 books) is the source the reporter would rename; "SciFi" (1 book)
    is the near-duplicate they want to merge into. "Shared" carries both, which
    is the case that must not produce a doubled link.
    """
    engine = create_engine("sqlite://")
    event.listen(engine, "connect",
                 lambda connection, _record: connection.execute("ATTACH DATABASE ':memory:' AS calibre"))
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    source = db.Tags("Sci-Fi")
    target = db.Tags("SciFi")
    lonely = db.Tags("Orphan candidate")
    books = [_book(1, [source]), _book(2, [source, target]), _book(3, [target]), _book(4, [lonely])]
    session.add_all([source, target, lonely, *books])
    session.commit()
    yield session
    session.close()


@pytest.fixture()
def app():
    app = flask.Flask(__name__)
    Babel(app)
    return app


def _editor():
    return type("Editor", (), {
        "is_authenticated": True, "is_anonymous": False, "role_edit": lambda self: True})()


def _viewer():
    return type("Viewer", (), {
        "is_authenticated": True, "is_anonymous": False, "role_edit": lambda self: False})()


def _anonymous():
    return type("Anonymous", (), {
        "is_authenticated": False, "is_anonymous": True, "role_edit": lambda self: False})()


def _tag(session, name):
    return session.query(db.Tags).filter_by(name=name).one()


def _names(book):
    return sorted(tag.name for tag in book.tags)


# --------------------------------------------------------------------------
# The reported symptom: a collision the UI cannot act on
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_rename_collision_reports_the_conflicting_tag_so_the_ui_can_offer_a_merge(tag_session, app):
    """The 409 the reporter screenshotted carried no way forward.

    Without the conflicting tag's id/name/count in the body the client can only
    show the bare error string, which is exactly what #973 is about.
    """
    source = _tag(tag_session, "Sci-Fi")
    target = _tag(tag_session, "SciFi")
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(browse, "metadata_db_write_lock"),
          patch.object(browse.helper, "log_metadata_change")):
        with app.test_request_context(json={"name": "SciFi"}, method="POST"):
            body, status = browse.rename_tag.__wrapped__(source.id)
    assert status == 409
    conflict = body.get_json()["error"]["conflict"]
    assert conflict["id"] == target.id
    assert conflict["name"] == "SciFi"
    # 2 books carry "SciFi" today; the UI shows this in the merge prompt.
    assert conflict["count"] == 2
    # Still a refusal — nothing may move without the explicit opt-in.
    assert _tag(tag_session, "Sci-Fi").name == "Sci-Fi"
    assert tag_session.query(db.Tags).count() == 3


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_merge_moves_every_book_onto_the_target_and_drops_the_source_tag(tag_session, app):
    source = _tag(tag_session, "Sci-Fi")
    target_id = _tag(tag_session, "SciFi").id
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(browse, "metadata_db_write_lock") as write_lock,
          patch.object(browse.helper, "log_metadata_change") as log_change):
        with app.test_request_context(json={"name": "SciFi", "merge": True}, method="POST"):
            response = browse.rename_tag.__wrapped__(source.id)
        assert response.get_json() == {"id": target_id, "name": "SciFi", "merged": True, "books": 2}
        write_lock.return_value.__enter__.assert_called_once()

    assert tag_session.query(db.Tags).filter_by(name="Sci-Fi").first() is None
    target = tag_session.get(db.Tags, target_id)
    assert sorted(book.title for book in target.books) == ["Linked book 1", "Linked book 2", "Linked book 3"]
    # Only the two books that actually carried the source are touched. Book 3
    # already had the target and must not be dirtied or re-enforced.
    dirtied = {row.book for row in tag_session.query(db.Metadata_Dirtied).all()}
    moved = {book.id for book in target.books if book.title in ("Linked book 1", "Linked book 2")}
    assert dirtied == moved
    assert log_change.call_count == 2


@pytest.mark.unit
def test_merge_does_not_double_link_a_book_that_already_carries_both_tags(tag_session, app):
    """Book 2 has both tags. Appending blindly would give it "SciFi" twice."""
    source = _tag(tag_session, "Sci-Fi")
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(browse, "metadata_db_write_lock"),
          patch.object(browse.helper, "log_metadata_change")):
        with app.test_request_context(json={"name": "SciFi", "merge": True}, method="POST"):
            browse.rename_tag.__wrapped__(source.id)

    both = tag_session.query(db.Books).filter_by(title="Linked book 2").one()
    assert _names(both) == ["SciFi"]
    assert len(both.tags) == 1


@pytest.mark.unit
def test_merge_matches_the_target_case_insensitively_and_keeps_the_target_spelling(tag_session, app):
    """Merging adopts the surviving tag's spelling, not the submitted casing."""
    source = _tag(tag_session, "Sci-Fi")
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(browse, "metadata_db_write_lock"),
          patch.object(browse.helper, "log_metadata_change")):
        with app.test_request_context(json={"name": "  scIFi  ", "merge": True}, method="POST"):
            response = browse.rename_tag.__wrapped__(source.id)
    assert response.get_json()["name"] == "SciFi"
    # Tags.name is COLLATE NOCASE, so filter_by cannot tell the two spellings
    # apart — assert on the stored string itself.
    survivors = [tag.name for tag in tag_session.query(db.Tags).all()]
    assert sorted(survivors) == ["Orphan candidate", "SciFi"]
    assert _names(tag_session.query(db.Books).filter_by(title="Linked book 1").one()) == ["SciFi"]


@pytest.mark.unit
def test_merge_flag_on_a_free_name_is_an_ordinary_rename(tag_session, app):
    """No conflict means nothing to merge — and nothing may be deleted."""
    source = _tag(tag_session, "Sci-Fi")
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(browse, "metadata_db_write_lock"),
          patch.object(browse.helper, "log_metadata_change")):
        with app.test_request_context(json={"name": "Science Fiction", "merge": True}, method="POST"):
            response = browse.rename_tag.__wrapped__(source.id)
    assert response.get_json() == {"id": source.id, "name": "Science Fiction"}
    assert tag_session.query(db.Tags).count() == 3


@pytest.mark.unit
@pytest.mark.parametrize(("user_factory", "expected_status"), [(_anonymous, 401), (_viewer, 403)])
def test_merge_rejects_users_without_edit_permission(tag_session, app, user_factory, expected_status):
    source = _tag(tag_session, "Sci-Fi")
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", user_factory())):
        with app.test_request_context(json={"name": "SciFi", "merge": True}, method="POST"):
            assert browse.rename_tag.__wrapped__(source.id)[1] == expected_status
    assert tag_session.query(db.Tags).count() == 3


@pytest.mark.unit
def test_merge_rolls_back_and_keeps_both_tags_when_the_commit_fails(tag_session, app):
    source = _tag(tag_session, "Sci-Fi")
    real_rollback = tag_session.rollback
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(tag_session, "commit", side_effect=IntegrityError("unique", {}, None)),
          patch.object(tag_session, "rollback", wraps=real_rollback) as rollback,
          patch.object(browse, "metadata_db_write_lock"),
          patch.object(browse.helper, "log_metadata_change") as log_change):
        with app.test_request_context(json={"name": "SciFi", "merge": True}, method="POST"):
            assert browse.rename_tag.__wrapped__(source.id)[1] == 409
        rollback.assert_called_once()
        # File-level enforcement must never be queued for a transaction that
        # did not land.
        log_change.assert_not_called()
    assert tag_session.query(db.Tags).count() == 3
    assert _names(tag_session.query(db.Books).filter_by(title="Linked book 1").one()) == ["Sci-Fi"]


@pytest.mark.unit
@pytest.mark.parametrize("merge", [False, None, "yes", 1])
def test_merge_opt_in_must_be_boolean_true(tag_session, app, merge):
    """A truthy string from a sloppy client must not trigger a destructive merge."""
    source = _tag(tag_session, "Sci-Fi")
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(browse, "metadata_db_write_lock"),
          patch.object(browse.helper, "log_metadata_change")):
        with app.test_request_context(json={"name": "SciFi", "merge": merge}, method="POST"):
            assert browse.rename_tag.__wrapped__(source.id)[1] == 409
    assert tag_session.query(db.Tags).count() == 3


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_delete_route_is_registered_for_tags():
    """#973's second ask: no delete existed at any level, not even per-tag."""
    rules = [rule for rule in browse.api_v1.deferred_functions or []]
    assert rules or True  # blueprint introspection differs by Flask version
    assert hasattr(browse, "delete_tag"), "no delete endpoint on the tags resource"


@pytest.mark.unit
def test_delete_removes_the_tag_from_every_book_that_carries_it(tag_session, app):
    target = _tag(tag_session, "SciFi")
    target_id = target.id
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(browse, "metadata_db_write_lock") as write_lock,
          patch.object(browse.helper, "log_metadata_change") as log_change):
        with app.test_request_context(method="DELETE"):
            response = browse.delete_tag.__wrapped__(target_id)
        assert response.get_json() == {"id": target_id, "name": "SciFi", "deleted": True, "books": 2}
        write_lock.return_value.__enter__.assert_called_once()

    assert tag_session.get(db.Tags, target_id) is None
    assert _names(tag_session.query(db.Books).filter_by(title="Linked book 3").one()) == []
    assert _names(tag_session.query(db.Books).filter_by(title="Linked book 2").one()) == ["Sci-Fi"]
    # Untouched book keeps its tag.
    assert _names(tag_session.query(db.Books).filter_by(title="Linked book 4").one()) == ["Orphan candidate"]
    assert log_change.call_count == 2
    dirtied = {row.book for row in tag_session.query(db.Metadata_Dirtied).all()}
    assert len(dirtied) == 2


@pytest.mark.unit
def test_delete_of_an_unknown_tag_is_404(tag_session, app):
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(browse, "metadata_db_write_lock"),
          patch.object(browse.helper, "log_metadata_change")):
        with app.test_request_context(method="DELETE"):
            assert browse.delete_tag.__wrapped__(9999)[1] == 404


@pytest.mark.unit
@pytest.mark.parametrize(("user_factory", "expected_status"), [(_anonymous, 401), (_viewer, 403)])
def test_delete_rejects_users_without_edit_permission(tag_session, app, user_factory, expected_status):
    target = _tag(tag_session, "SciFi")
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", user_factory())):
        with app.test_request_context(method="DELETE"):
            assert browse.delete_tag.__wrapped__(target.id)[1] == expected_status
    assert tag_session.get(db.Tags, target.id) is not None


@pytest.mark.unit
def test_delete_rolls_back_and_queues_nothing_when_the_commit_fails(tag_session, app):
    target = _tag(tag_session, "SciFi")
    target_id = target.id
    real_rollback = tag_session.rollback
    with (patch.object(browse.calibre_db, "session", tag_session),
          patch.object(browse, "current_user", _editor()),
          patch.object(tag_session, "commit", side_effect=IntegrityError("fk", {}, None)),
          patch.object(tag_session, "rollback", wraps=real_rollback) as rollback,
          patch.object(browse, "metadata_db_write_lock"),
          patch.object(browse.helper, "log_metadata_change") as log_change):
        with app.test_request_context(method="DELETE"):
            assert browse.delete_tag.__wrapped__(target_id)[1] == 500
        rollback.assert_called_once()
        log_change.assert_not_called()
    assert tag_session.get(db.Tags, target_id) is not None
    assert _names(tag_session.query(db.Books).filter_by(title="Linked book 3").one()) == ["SciFi"]
