# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entity-list browse endpoints for /api/v1."""
from flask import jsonify, request
from flask_babel import gettext as _
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from . import api_v1
from .. import calibre_db, db, helper
from ..cw_login import current_user
from ..services.calibre_db_lock import metadata_db_write_lock
from ..usermanagement import login_required_if_no_ano


@api_v1.route("/authors")
@login_required_if_no_ano
def list_authors():
    rows = (calibre_db.session.query(db.Authors, func.count('books_authors_link.book').label('count'))
            .join(db.books_authors_link)
            .join(db.Books)
            .filter(calibre_db.common_filters())
            .group_by(text('books_authors_link.author'))
            .order_by(func.ng_sort_key(db.Authors.sort), db.Authors.sort, db.Authors.id)
            .all())
    items = [{"id": a.id, "name": a.name.replace("|", ","), "count": cnt} for a, cnt in rows]
    return {"items": items}


@api_v1.route("/series")
@login_required_if_no_ano
def list_series():
    rows = (calibre_db.session.query(db.Series, func.count('books_series_link.book').label('count'))
            .join(db.books_series_link)
            .join(db.Books)
            .filter(calibre_db.common_filters())
            .group_by(text('books_series_link.series'))
            .order_by(func.ng_sort_key(db.Series.sort), db.Series.sort, db.Series.id)
            .all())
    items = [{"id": s.id, "name": s.name, "count": cnt} for s, cnt in rows]
    return {"items": items}


@api_v1.route("/tags")
@login_required_if_no_ano
def list_tags():
    rows = (calibre_db.session.query(db.Tags, func.count('books_tags_link.book').label('count'))
            .join(db.books_tags_link)
            .join(db.Books)
            .filter(calibre_db.common_filters())
            .group_by(db.Tags.id)
            .order_by(func.ng_sort_key(db.Tags.name), db.Tags.name, db.Tags.id)
            .all())
    items = [{"id": t.id, "name": t.name, "count": cnt} for t, cnt in rows]
    return {"items": items}


def _require_metadata_editor():
    """Return an error response when the caller may not edit metadata, else None."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return jsonify({"error": {"code": "unauthorized", "message": _("You must be signed in")}}), 401
    if not current_user.role_edit():
        return jsonify({"error": {"code": "forbidden", "message": _("You are not allowed to edit metadata")}}), 403
    return None


@api_v1.route("/tags/<int:tag_id>", methods=["POST"])
@login_required_if_no_ano
def rename_tag(tag_id):
    """Rename one existing Calibre tag for every linked book.

    A name collision is the de-duplication case (#973), not merely an error:
    renaming a tag onto its near-duplicate is how you consolidate the two. So
    the 409 carries the conflicting tag (id, name, how many books it holds) and
    the caller can repeat the request with ``merge: true`` to fold this tag into
    it. The merge stays opt-in and must be boolean ``true`` — folding two tags
    together destroys the distinction between them and there is no undo, so a
    typo that happens to match an existing tag must not silently consume it.
    """
    guard = _require_metadata_editor()
    if guard is not None:
        return guard

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return jsonify({"error": {"code": "invalid_request", "message": _("Tag name must be text")}}), 400
    name = payload["name"].strip()
    if not name:
        return jsonify({"error": {"code": "invalid_request", "message": _("Tag name cannot be empty")}}), 400
    if "," in name:
        return jsonify({"error": {"code": "invalid_request", "message": _("Tag name cannot contain commas")}}), 400
    merge = payload.get("merge") is True

    with metadata_db_write_lock():
        tag = calibre_db.session.get(db.Tags, tag_id)
        if tag is None:
            return jsonify({"error": {"code": "not_found", "message": _("Tag not found")}}), 404
        if tag.name == name:
            return jsonify({"id": tag.id, "name": tag.name})

        # Recheck under the process-shared writer lock so two Flask workers
        # cannot both pass the uniqueness check before either commits.
        duplicate = (calibre_db.session.query(db.Tags)
                     .filter(func.lower(db.Tags.name) == name.lower(), db.Tags.id != tag_id)
                     .first())
        if duplicate is not None and not merge:
            # The count is the raw link count, i.e. the true blast radius of the
            # merge this 409 is offering — deliberately not narrowed by
            # common_filters(), which would under-report what actually moves.
            return jsonify({"error": {
                "code": "conflict",
                "message": _("A tag with that name already exists"),
                "conflict": {"id": duplicate.id, "name": duplicate.name, "count": len(duplicate.books)},
            }}), 409

        # Materialize the exact linked-book set while association writers are
        # excluded, then mutate and dirty that same set in one transaction.
        affected_books = list(tag.books)
        try:
            if duplicate is not None:
                # Merge: move every book off this tag onto the survivor, whose
                # own spelling wins, then drop the now-empty tag. A book already
                # carrying both must end up with one link, not two.
                for book in affected_books:
                    if duplicate not in book.tags:
                        book.tags.append(duplicate)
                    book.tags.remove(tag)
                    helper.mark_book_modified(book, set_dirty=True)
                calibre_db.session.delete(tag)
                result = {"id": duplicate.id, "name": duplicate.name,
                          "merged": True, "books": len(affected_books)}
            else:
                tag.name = name
                for book in affected_books:
                    helper.mark_book_modified(book, set_dirty=True)
                result = {"id": tag.id, "name": tag.name}
            calibre_db.session.commit()
        except IntegrityError:
            calibre_db.session.rollback()
            return jsonify({"error": {"code": "conflict", "message": _("A tag with that name already exists")}}), 409
        except Exception:
            calibre_db.session.rollback()
            raise

    # File-level enforcement is best-effort and must only be queued after the
    # database transaction succeeds. Every linked book now carries the surviving
    # tag name (or, after a merge, no longer carries the one that was folded in).
    for book in affected_books:
        helper.log_metadata_change(book, {"tags": ", ".join(item.name for item in book.tags)})
    return jsonify(result)


@api_v1.route("/tags/<int:tag_id>", methods=["DELETE"])
@login_required_if_no_ano
def delete_tag(tag_id):
    """Remove one Calibre tag from every book that carries it (#973).

    The Tag view offered no way to get rid of a tag at all — not from the list
    and not from the tag's own page — so a mistyped or obsolete tag was
    permanent. Only the tag is deleted; the books it pointed at are untouched
    apart from losing it.
    """
    guard = _require_metadata_editor()
    if guard is not None:
        return guard

    with metadata_db_write_lock():
        tag = calibre_db.session.get(db.Tags, tag_id)
        if tag is None:
            return jsonify({"error": {"code": "not_found", "message": _("Tag not found")}}), 404

        name = tag.name
        affected_books = list(tag.books)
        try:
            for book in affected_books:
                book.tags.remove(tag)
                helper.mark_book_modified(book, set_dirty=True)
            calibre_db.session.delete(tag)
            calibre_db.session.commit()
        except Exception:
            calibre_db.session.rollback()
            return jsonify({"error": {"code": "server_error",
                                      "message": _("Tag could not be deleted")}}), 500

    for book in affected_books:
        helper.log_metadata_change(book, {"tags": ", ".join(item.name for item in book.tags)})
    return jsonify({"id": tag_id, "name": name, "deleted": True, "books": len(affected_books)})


@api_v1.route("/publishers")
@login_required_if_no_ano
def list_publishers():
    rows = (calibre_db.session.query(db.Publishers, func.count(db.books_publishers_link.c.book).label('count'))
            .join(db.books_publishers_link, db.Publishers.id == db.books_publishers_link.c.publisher)
            .join(db.Books, db.books_publishers_link.c.book == db.Books.id)
            .filter(calibre_db.common_filters())
            .group_by(db.Publishers.id)
            .order_by(func.ng_sort_key(db.Publishers.sort), db.Publishers.sort, db.Publishers.id)
            .all())
    items = [{"id": p.id, "name": p.name, "count": cnt} for p, cnt in rows]
    return {"items": items}


@api_v1.route("/languages")
@login_required_if_no_ano
def list_languages():
    lang_list = calibre_db.speaking_language(with_count=True)
    # speaking_language returns [[Category, count], ...] where Category.id = lang_code, Category.name = display name
    items = [{"id": cat.id, "name": cat.name, "count": cnt} for cat, cnt in lang_list]
    return {"items": items}


@api_v1.route("/ratings")
@login_required_if_no_ano
def list_ratings():
    """Browse by star rating. Calibre stores rating as 0-10 (stars*2); the SPA
    filters books by the Ratings row id (matches list_books ?rating=)."""
    rows = (calibre_db.session.query(db.Ratings, func.count('books_ratings_link.book').label('count'))
            .join(db.books_ratings_link)
            .join(db.Books)
            .filter(calibre_db.common_filters())
            .group_by(text('books_ratings_link.rating'))
            .order_by(db.Ratings.rating.desc())
            .all())
    items = [{"id": r.id, "name": "%g★" % (r.rating / 2), "count": cnt} for r, cnt in rows]
    return {"items": items}


@api_v1.route("/formats")
@login_required_if_no_ano
def list_formats():
    """Browse by file format (EPUB, PDF, …). The format string is the id; the SPA
    filters books by it (matches list_books ?format=)."""
    rows = (calibre_db.session.query(db.Data.format, func.count(db.Data.book).label('count'))
            .join(db.Books, db.Books.id == db.Data.book)
            .filter(calibre_db.common_filters())
            .group_by(db.Data.format)
            .order_by(db.Data.format)
            .all())
    items = [{"id": fmt, "name": fmt, "count": cnt} for fmt, cnt in rows]
    return {"items": items}
