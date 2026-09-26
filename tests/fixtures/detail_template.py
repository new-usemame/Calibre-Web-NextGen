# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the real classic book page (``cps/templates/detail.html``) outside Flask.

The page's own conditionals are what the tests exercise, so the production
template runs under Jinja as it is. Only the layout it extends, the filters and
the view's context are stand-ins: a reader who owns no shelves, sees none, and
holds no roles, looking at a book with no files or metadata. Tests override the
parts they are about.
"""

from pathlib import Path
from types import SimpleNamespace

from jinja2 import DictLoader, Environment

ROOT = Path(__file__).resolve().parents[2]


def reader(**overrides):
    """A signed-in reader with no roles; each role is a method, as on ub.User."""
    attributes = dict(
        id=7,
        is_anonymous=False,
        is_authenticated=True,
        kindle_mail="",
        allow_additional_ereader_emails=False,
        shelf=SimpleNamespace(all=lambda: []),
        library_mode=lambda: "monolibrary",
        role_edit=lambda: False,
        role_viewer=lambda: False,
        role_download=lambda: False,
        role_browse_global=lambda: False,
        role_delete_books=lambda: False,
        role_admin=lambda: False,
        role_edit_shelfs=lambda: False,
        check_visibility=lambda *_args: False,
    )
    attributes.update(overrides)
    return SimpleNamespace(**attributes)


def shelf(shelf_id, name, *, owner, public=False):
    return SimpleNamespace(id=shelf_id, name=name, user_id=owner, is_public=1 if public else 0)


def render_detail(*, user=None, shelves_access=(), books_shelfs=(), **context):
    """Render detail.html for ``user`` (default: :func:`reader`).

    ``shelves_access`` is what the app puts in ``g.shelves_access`` (public
    shelves plus the reader's own) and ``books_shelfs`` the ids of every shelf
    holding the book, whoever owns it.
    """
    source = (ROOT / "cps" / "templates" / "detail.html").read_text(encoding="utf-8")
    base = "{% block header %}{% endblock %}{% block body %}{% endblock %}"
    environment = Environment(
        loader=DictLoader({"detail.html": source, "layout.html": base, "fragment.html": base}),
        autoescape=True,
    )
    environment.filters.update({
        "yesno": lambda value, yes, no: yes if value else no,
        "last_modified": lambda _value: "",
        "clean_string": lambda value: value,
        "escapedlink": lambda value: value,
        "filesizeformat_binary": lambda value: value,
        "formatdate": lambda value, *_args: value or "",
        "formatfloat": lambda value, *_args: value,
    })
    entry = SimpleNamespace(
        id=42,
        title="Rendered Book",
        uuid="rendered-book",
        comments=[],
        reader_list=[],
        data=[],
        email_share_list=[],
        read_status=False,
        is_archived=False,
        ordered_authors=[],
        read_status_raw=0,
        ratings=[],
        identifiers=[],
        tags=[],
        series=[],
        series_index=None,
        languages=[],
        publishers=[],
        pubdate=None,
        timestamp=None,
        last_modified=None,
    )
    values = dict(
        is_xhr=False,
        title=entry.title,
        entry=entry,
        current_user=user or reader(),
        g=SimpleNamespace(
            current_theme=0,
            shelves_access=list(shelves_access),
            user_hide_enabled=False,
            config_user_hide_enabled=False,
        ),
        config=SimpleNamespace(config_user_hide_enabled=False),
        books_shelfs=list(books_shelfs),
        cc={},
        cwa_settings=SimpleNamespace(),
        kosync_progress=None,
        kosync_progress_timestamp=None,
        kosync_progress_created_at=None,
        is_hidden=False,
        is_favorited=False,
        other_users_with_kindle=[],
        original_filename=None,
        _=lambda text, **kwargs: text % kwargs if kwargs else text,
        url_for=lambda endpoint, **kwargs: "/" + endpoint + "".join(
            f"/{key}={value}" for key, value in sorted(kwargs.items())),
        csrf_token=lambda: "token",
        format_type=lambda value: value,
        formatfloat=lambda value, *_args: value,
        delete_book=lambda *_args: "",
    )
    values.update(context)
    return environment.get_template("detail.html").render(**values)
