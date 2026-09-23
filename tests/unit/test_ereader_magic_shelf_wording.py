# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Magic Shelf sync setting names what it reaches.

"Sync Magic Shelves to Kobo" also decides whether magic shelves reach the
KOReader library (``cps/services/koreader_library.py`` reads the same
``config_kobo_sync_magic_shelves``). Once KOReader sync is on, the setting, its
explanation, and the warning a reader gets for marking a magic shelf while it
is off all say e-readers; with only Kobo sync they keep their Kobo wording and
its translations.
"""

from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest
from lxml import html

from cps import ub
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "cps" / "templates"
EREADER_LABEL = "Sync Magic Shelves to e-readers (Kobo and KOReader)"
KOBO_LABEL = "Sync Magic Shelves to Kobo"


def _render_settings(**context):
    environment = jinja2.Environment(
        loader=jinja2.ChoiceLoader([
            jinja2.DictLoader({
                "layout.html": ("{% block flash %}{% endblock %}{% block header %}{% endblock %}"
                                "{% block body %}{% endblock %}"),
            }),
            jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        ]),
        autoescape=True,
    )
    return environment.get_template("cwa_settings.html").render(
        _=lambda message, **values: message % values if values else message,
        autoingest_options=(),
        config=SimpleNamespace(config_timezone="UTC", hardcover_sync_enabled=lambda: False),
        cwa_settings=defaultdict(lambda: False),
        hardcover_token_available=False,
        ignorable_formats=(),
        next_duplicate_scan_run=None,
        target_formats=(),
        title="CWA Settings",
        url_for=lambda endpoint: "/%s" % endpoint,
        **context,
    )


@pytest.mark.parametrize("koreader_sync, label", [(True, EREADER_LABEL), (False, KOBO_LABEL)])
def test_the_admin_setting_names_what_it_reaches(koreader_sync, label):
    document = html.fromstring(_render_settings(koreader_sync=koreader_sync))
    [element] = document.xpath('//label[@for="config_kobo_sync_magic_shelves"]')
    assert element.text_content().strip() == label
    explanation = element.xpath('following-sibling::p[contains(@class, "cwa-settings-tooltip")][1]')
    assert ("KOReader" in explanation[0].text_content()) is koreader_sync
    [checkbox] = document.xpath('//input[@id="config_kobo_sync_magic_shelves"]')
    assert ("KOReader" in checkbox.get("title")) is koreader_sync


@pytest.fixture
def world(monkeypatch, tmp_path):
    built = LibraryWorld(monkeypatch, tmp_path)
    built.enable_web()
    built.add_user("alice")
    yield built
    built.close()


def _mark(world, **config):
    from cps import config as app_config
    for name, value in config.items():
        world.monkeypatch.setattr(app_config, name, value, raising=False)
    alice = world.session.query(ub.User).filter(ub.User.name == "alice").one()
    shelf = ub.MagicShelf(name="Unread sci-fi", user_id=alice.id,
                          rules={"condition": "AND", "rules": []})
    world.session.add(shelf)
    world.session.commit()
    response = world.browser("alice").post("/api/v1/magicshelf/%d/kobo-sync" % shelf.id,
                                           json={"kobo_sync": True})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def test_marking_a_magic_shelf_while_their_sync_is_off_names_the_setting_as_shown(world):
    body = _mark(world, config_kobo_sync_magic_shelves=False, config_kobo_sync=False)
    assert EREADER_LABEL in body["warning"]
    assert "e-readers" in body["warning"]


def test_with_only_kobo_sync_the_warning_keeps_its_kobo_wording(world):
    world.sync_switch(False)
    body = _mark(world, config_kobo_sync_magic_shelves=False, config_kobo_sync=True)
    assert KOBO_LABEL in body["warning"]
    assert "KOReader" not in body["warning"]
