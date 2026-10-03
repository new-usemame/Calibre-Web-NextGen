# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart shelves retain their name across web and OPDS entry points (#1498)."""

import subprocess
from pathlib import Path
from flask import Flask, request
from flask_babel import Babel, gettext
from werkzeug.routing import Rule
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(params=["en", "nl", "fr"])
def locale_context(request, tmp_path):
    for locale in ["nl", "fr"]:
        po = (
            Path(__file__).resolve().parents[2]
            / "cps/translations"
            / locale
            / "LC_MESSAGES/messages.po"
        )
        mo = tmp_path / locale / "LC_MESSAGES/messages.mo"
        mo.parent.mkdir(parents=True)
        subprocess.run(["msgfmt", "-o", str(mo), str(po)], check=True)
    app = Flask(__name__)
    Babel(
        app,
        default_translation_directories=str(tmp_path),
        locale_selector=lambda: request.param,
    )
    with app.test_request_context("/"):
        yield


def test_opds_smart_shelf_parent_and_index_match_new_ui(locale_context):
    from cps import opds

    root = opds.OPDS_ROOT_ENTRY_DEFS["magic_shelves"]
    assert str(root["title"]) == gettext("Smart shelves")
    assert root["endpoint"] == "opds.feed_magic_shelfindex"
    request.url_rule = Rule("/opds/magicshelf", endpoint="opds.feed_magic_shelfindex")
    assert opds._opds_feed_title_for_endpoint(request.endpoint) == gettext(
        "Smart shelves"
    )


@pytest.mark.parametrize("public", [False, True])
def test_opds_smart_shelf_entry_uses_the_localized_type_name(locale_context, public):
    from types import SimpleNamespace
    from xml.etree import ElementTree
    from flask import current_app, render_template
    from jinja2 import FileSystemLoader
    from cps.jinjia import jinjia

    current_app.register_blueprint(jinjia)
    current_app.jinja_loader = FileSystemLoader(
        str(Path(__file__).resolve().parents[2] / "cps/templates")
    )
    current_app.jinja_env.globals.update(
        url_for=lambda *_, **__: "/opds",
        opds_search_url_path=lambda: "/opds/search",
    )
    shelf = SimpleNamespace(
        id=17, name="A & <Probe>", icon="🪄", is_magic_shelf=True,
        is_public=int(public), opds_url="/opds/magicshelf/17",
    )
    document = ElementTree.fromstring(render_template(
        "feed.xml", listelements=[shelf], entries=[], letterelements=[],
        pagination=None, instance="Library", feed_title=gettext("Smart shelves"),
        current_time="2026-10-03T00:00:00Z",
    ))
    entry = document.find("{http://www.w3.org/2005/Atom}entry")
    expected = "🪄 A & <Probe> (" + gettext("Smart shelves") + ")"
    if public:
        expected += " " + gettext("(Public)")
    assert entry.find("{http://www.w3.org/2005/Atom}title").text == expected
    assert entry.find("{http://www.w3.org/2005/Atom}link").get("href") == shelf.opds_url


_SMART_COPY = [
    "Smart-shelf sync is disabled globally. Enable “%(setting)s” in CWA Settings so this shelf can reach your e-readers.",
    "Choose how your smart shelves are ordered in the sidebar.",
    "Expose this smart shelf in my OPDS feed",
    "Smart shelves",
    "Create smart shelf",
    "Edit smart shelf",
    "Smart shelves order",
    "Smart shelves visibility",
    "Smart shelf editing works best on desktop or tablet devices.",
    "What are smart shelves?",
    "Smart shelves collect books automatically using rules.",
    "Smart shelves were called Magic Shelves in older versions.",
    "Smart shelf — %(icon)s %(name)s",
    "Error loading smart shelf",
    "Books organized in smart shelves",
    "Sync smart shelves to e-readers (Kobo and KOReader)",
    "Sync smart shelves to Kobo",
    "When enabled, marked smart shelves reach your Kobo as collections. Enable Kobo Sync in Basic Configuration too.",
    "KOReader also receives marked smart shelves in its library.",
    "Turn on “%(setting)s” in CWA Settings first. Until then, this checkbox has no effect and the shelf will not reach your e-reader.",
]
_LOCALES = sorted(
    p.parts[-3]
    for p in (Path(__file__).resolve().parents[2] / "cps/translations").glob(
        "*/LC_MESSAGES/messages.po"
    )
)


@pytest.mark.parametrize("locale", _LOCALES)
def test_renamed_smart_shelf_copy_is_present_in_compiled_catalog(locale):
    import gettext as stdlib_gettext
    from io import BytesIO

    po = (
        Path(__file__).resolve().parents[2]
        / "cps/translations"
        / locale
        / "LC_MESSAGES/messages.po"
    )
    result = subprocess.run(
        ["msgfmt", "--check-format", "-o", "-", str(po)],
        capture_output=True,
        check=True,
    )
    catalog = stdlib_gettext.GNUTranslations(BytesIO(result.stdout))
    assert not [
        message for message in _SMART_COPY if message not in catalog._catalog
    ], locale
    assert catalog.gettext("Smart shelf — %(icon)s %(name)s") % {
        "icon": "🪄",
        "name": "A & B",
    }
    assert catalog.gettext(_SMART_COPY[-1]) % {"setting": "Sync"}


def test_koreader_unreadable_shelf_scope_keeps_the_smart_name(monkeypatch, tmp_path):
    """The device error names the same shelves as the web controls (#1498)."""
    from tests.unit.koreader_library_world import LibraryWorld
    from cps import kobo

    world = LibraryWorld(monkeypatch, tmp_path)
    try:
        world.add_user("reader", shelf_only=True)
        world.add_book(1, "Anything")
        monkeypatch.setattr(
            kobo, "get_magic_shelf_book_ids_for_kobo", lambda _: ({1}, False)
        )
        response = world.client.get(
            "/kosync/syncs/library", headers=world.device_headers("reader")
        )
        assert response.status_code == 503
        body = response.get_json()
        assert body["error"] == "scope_unavailable"
        assert "smart shelves" in body["message"].lower()
        assert "magic shelves" not in body["message"].lower()
    finally:
        world.close()
