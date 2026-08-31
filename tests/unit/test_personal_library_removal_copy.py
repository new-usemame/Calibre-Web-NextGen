# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Truth-in-copy contract for the My Library removal confirmation."""

import gettext
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from jinja2 import DictLoader, Environment


ROOT = Path(__file__).resolve().parents[2]
OLD_DEVICE_CLAIM = (
    "The next time your e-reader updates, this book disappears from it."
)
CATALOG_EFFECT = "It leaves your library and your OPDS feed."
DEVICE_EFFECT = (
    "If you use Kobo's built-in sync, it also leaves your Kobo at its next "
    "sync. Other e-readers keep downloaded copies, and KOReader progress sync "
    "keeps working."
)
TRANSLATIONS = {
    "fr": {
        CATALOG_EFFECT: "Il disparaît de votre bibliothèque et de votre flux OPDS.",
        DEVICE_EFFECT: (
            "Si vous utilisez la synchronisation intégrée de Kobo, il disparaît "
            "également de votre Kobo lors de la prochaine synchronisation. Les "
            "autres liseuses conservent les exemplaires téléchargés et la "
            "synchronisation de la progression KOReader continue de fonctionner."
        ),
    },
    "nl": {
        CATALOG_EFFECT: "Het verdwijnt uit je bibliotheek en je OPDS-feed.",
        DEVICE_EFFECT: (
            "Als je de ingebouwde synchronisatie van Kobo gebruikt, verdwijnt het "
            "ook van je Kobo bij de volgende synchronisatie. Andere e-readers "
            "behouden gedownloade exemplaren en de voortgangssynchronisatie van "
            "KOReader blijft werken."
        ),
    },
}


pytestmark = pytest.mark.unit


def _render_detail_for_personal_library():
    """Render the real detail template body with its production conditionals."""
    source = (ROOT / "cps" / "templates" / "detail.html").read_text(
        encoding="utf-8"
    )
    base = "{% block header %}{% endblock %}{% block body %}{% endblock %}"
    environment = Environment(
        loader=DictLoader({
            "detail.html": source,
            "layout.html": base,
            "fragment.html": base,
        }),
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
    user = SimpleNamespace(
        id=7,
        is_anonymous=False,
        is_authenticated=True,
        kindle_mail="",
        allow_additional_ereader_emails=False,
        shelf=SimpleNamespace(all=lambda: []),
        library_mode=lambda: "personal_library",
        role_edit=lambda: False,
        role_viewer=lambda: False,
        role_download=lambda: False,
        role_browse_global=lambda: True,
        role_delete_books=lambda: False,
        role_admin=lambda: False,
        role_edit_shelfs=lambda: False,
        check_visibility=lambda *_args: False,
    )
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
    return environment.get_template("detail.html").render(
        is_xhr=False,
        title=entry.title,
        entry=entry,
        current_user=user,
        g=SimpleNamespace(
            current_theme=0,
            shelves_access=[],
            user_hide_enabled=False,
            config_user_hide_enabled=False,
        ),
        config=SimpleNamespace(config_user_hide_enabled=False),
        books_shelfs=[],
        cc={},
        cwa_settings=SimpleNamespace(),
        kosync_progress=None,
        kosync_progress_timestamp=None,
        kosync_progress_created_at=None,
        is_hidden=False,
        is_favorited=False,
        other_users_with_kindle=[],
        original_filename=None,
        _=lambda text, **values: text % values if values else text,
        url_for=lambda endpoint, **_values: "/" + endpoint,
        csrf_token=lambda: "token",
        format_type=lambda value: value,
        formatfloat=lambda value, *_args: value,
        delete_book=lambda *_args: "",
    )


def test_classic_personal_library_modal_renders_client_specific_effects():
    """The actual personal-library modal must contain both truthful paragraphs."""
    rendered = _render_detail_for_personal_library()
    modal = BeautifulSoup(rendered, "html.parser").select_one(
        "#removeFromMyLibraryModal"
    )
    assert modal is not None
    paragraphs = [
        paragraph.get_text(" ", strip=True)
        for paragraph in modal.select(".modal-body p")
    ]
    assert paragraphs[:2] == [CATALOG_EFFECT, DEVICE_EFFECT]
    assert OLD_DEVICE_CLAIM not in modal.get_text(" ", strip=True)


def test_spa_only_copy_is_anchored_for_babel_extraction():
    anchors = (ROOT / "cps" / "spa_strings.py").read_text(encoding="utf-8")
    assert CATALOG_EFFECT in anchors
    assert DEVICE_EFFECT in anchors


@pytest.mark.parametrize("locale", ("fr", "nl"))
def test_removal_copy_is_translated_in_the_compiled_catalog(locale, tmp_path):
    """Read the runtime .mo result; a populated .po entry alone is not proof."""
    po = ROOT / "cps" / "translations" / locale / "LC_MESSAGES" / "messages.po"
    mo = tmp_path / "messages.mo"
    subprocess.run(
        ["msgfmt", "--check", po, "-o", mo], check=True, capture_output=True
    )
    with mo.open("rb") as handle:
        catalog = gettext.GNUTranslations(handle)

    for msgid, expected in TRANSLATIONS[locale].items():
        assert catalog.gettext(msgid) == expected
