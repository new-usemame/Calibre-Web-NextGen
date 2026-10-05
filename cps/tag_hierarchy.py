# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only built-in tag browsing, honoring Calibre's category preference.

Visible book/tag pairs supply both counts and membership. Synthetic parents
are browse groups, never editable metadata rows or permission overrides.
"""
import json

from flask_babel import gettext as _
from sqlalchemy import Integer, false, literal_column, text

from . import db, hierarchy

MAX_GROUP_PATH_LENGTH = 4096
MAX_GROUP_DEPTH = 64


def is_configured(calibre_db):
    """An absent table/row means Calibre's default: flat built-in tags.

    Malformed data and database failures remain errors for the caller to expose
    as retryable unavailability. Custom-column legacy detection is unrelated.
    """
    calibre_db.ensure_session()
    attached = calibre_db.session.execute(text("PRAGMA database_list")).all()
    schema = "calibre" if any(row[1] == "calibre" for row in attached) else "main"
    exists = calibre_db.session.execute(text(
        "SELECT 1 FROM " + schema + ".sqlite_master "
        "WHERE type='table' AND name='preferences'"
    )).first()
    if not exists:
        return False
    raw = calibre_db.session.execute(text(
        "SELECT val FROM " + schema + ".preferences WHERE key='categories_using_hierarchy'"
    )).scalar()
    categories = json.loads(raw) if raw is not None else []
    if not isinstance(categories, list) or not all(isinstance(item, str) for item in categories):
        raise ValueError("Invalid Calibre categories_using_hierarchy preference")
    return "tags" in categories


class TagTree:
    """One request's visible tree and exact metadata-ID membership."""

    def __init__(self, configured, pairs):
        self.hierarchical = configured
        self._paths = {}
        roots = []
        records = {}
        for book_id, tag_id, name in pairs:
            parts = hierarchy.split_path(name) if configured else []
            # Rendering and group URLs have finite bounds. Keep unusual but
            # valid Calibre values browsable as exact records instead of
            # breaking every sibling or advertising an unreachable group.
            if len(parts) > MAX_GROUP_DEPTH or len(".".join(parts)) > MAX_GROUP_PATH_LENGTH:
                parts = []
            if not parts:
                node = records.setdefault(tag_id, {
                    "id": tag_id, "name": name if name.strip() else _("Unnamed tag"),
                    "path": None, "children": [], "_direct": set(),
                    "_books": set(), "_tags": {tag_id},
                })
                node["_direct"].add(book_id)
                node["_books"].add(book_id)
                continue
            parent = None
            for depth, part in enumerate(parts):
                path = ".".join(parts[:depth + 1])
                node = self._paths.get(path)
                if node is None:
                    node = {"id": None, "name": part, "path": path, "children": [],
                            "_direct": set(), "_books": set(), "_tags": set()}
                    self._paths[path] = node
                    (parent["children"] if parent else roots).append(node)
                parent = node
            node["_direct"].add(book_id)
            node["_books"].add(book_id)
            node["_tags"].add(tag_id)
        # Descendants are finalized first without recursive parser traversal.
        ordered = sorted(self._paths.values(), key=lambda node: node["path"].count("."), reverse=True)
        for node in ordered:
            for child in node["children"]:
                node["_books"].update(child["_books"])
                node["_tags"].update(child["_tags"])
        public = {}
        for node in ordered + list(records.values()):
            public[id(node)] = {
                "id": node["id"], "name": node["name"], "path": node["path"],
                "count": len(node["_direct"]), "total_count": len(node["_books"]),
                "children": [public[id(child)] for child in sorted(
                    node["children"], key=lambda child: child["name"].casefold())],
            }
        self.items = [public[id(node)] for node in sorted(
            roots + list(records.values()), key=lambda node: (node["name"].casefold(), node["id"] or 0))]
        self._public_paths = {path: public[id(node)] for path, node in self._paths.items()}

    def get_node(self, path):
        """Resolve by canonical path, never a translated opaque-leaf label."""
        return self._public_paths.get(".".join(hierarchy.split_path(path)))

    def book_filter(self, path):
        """Exact subtree IDs; callers still apply their ordinary visibility.

        IDs originate exclusively in typed database rows. Integer SQL literals
        avoid SQLite's bind-variable limit on large tag trees; no request text
        or stored tag name enters SQL syntax. LIKE and NOCASE name comparisons
        would select values that the displayed tree never counted.
        """
        if not isinstance(path, str) or not path or len(path) > MAX_GROUP_PATH_LENGTH:
            raise LookupError("Tag group not found")
        node = self._paths.get(".".join(hierarchy.split_path(path)))
        if node is None:
            raise LookupError("Tag group not found")
        tag_ids = sorted(node["_tags"])
        if not tag_ids:
            return false()
        if any(type(tag_id) is not int or tag_id < 0 for tag_id in tag_ids):
            raise ValueError("Invalid metadata tag ID")
        return db.Books.tags.any(db.Tags.id.in_(
            [literal_column(str(tag_id), type_=Integer()) for tag_id in tag_ids]))


def read_tree(calibre_db, book_filter=None):
    configured = is_configured(calibre_db)
    if book_filter is None:
        book_filter = calibre_db.common_filters()
    pairs = (calibre_db.session.query(db.Books.id, db.Tags.id, db.Tags.name)
             .select_from(db.Books).join(db.books_tags_link).join(db.Tags)
             .filter(book_filter).distinct().all())
    return TagTree(configured, pairs)
