"""Library contents and OPDS exposure render on the user form with no integration enabled.

They used to sit inside the "OAuth & API Integrations" card, which renders only when
Kobo sync, Hardcover or an OAuth provider is on. With all of them off an administrator
could not switch a user to My Library from the classic page, and every save of that
form or of /me cleared the account's OPDS shelf exposure, because the handlers read an
absent checkbox as "off" (cps/admin.py and cps/web.py).
"""
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import render_template
from jinja2 import ChainableUndefined, ChoiceLoader, DictLoader, FileSystemLoader

from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit


class InputIds(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'input' and dict(attrs).get('id'):
            self.ids.add(dict(attrs)['id'])


def _render(monkeypatch, tmp_path, *, kobo_support, profile, new_user):
    from cps import config
    world = LibraryWorld(monkeypatch, tmp_path)
    try:
        user = world.add_user('alice')
        world.app.jinja_loader = ChoiceLoader([
            DictLoader({'layout.html': '{% block body %}{% endblock %}'}),
            FileSystemLoader(Path(__file__).resolve().parents[2] / 'cps/templates'),
        ])
        # The existing-account page reads many view-only lists (shelves, columns,
        # OPDS order) this test does not care about; let those default to empty.
        # Every condition this test is about gets a real value below.
        world.app.jinja_env.undefined = ChainableUndefined
        with world.app.test_request_context('/admin/user/1'):
            html = render_template('user_edit.html', content=user, current_user=user,
                                   config=defaultdict(lambda: False, vars(config)),
                                   new_user=new_user, profile=profile,
                                   kobo_support=kobo_support, hardcover_support=False,
                                   registered_oauth={}, oauth_status={},
                                   url_for=lambda *args, **kwargs: '/unused',
                                   csrf_token=lambda: 'test',
                                   opds_root_labels={}, magic_shelf_order_labels={},
                                   visible_public_shelves=[], hidden_custom_shelves=[])
    finally:
        world.close()
    parser = InputIds()
    parser.feed(html)
    return parser.ids


@pytest.mark.parametrize('profile', [False, True], ids=['admin-edit', 'own-profile'])
@pytest.mark.parametrize('kobo_support', [False, True], ids=['no-integrations', 'kobo-on'])
def test_existing_account_form_offers_library_contents(monkeypatch, tmp_path, kobo_support, profile):
    ids = _render(monkeypatch, tmp_path, kobo_support=kobo_support, profile=profile, new_user=False)
    assert {'library_mode_monolibrary', 'library_mode_personal', 'opds_only_shelves_sync'} <= ids


def test_new_user_form_still_has_no_library_contents(monkeypatch, tmp_path):
    # The new-user handler ignores library_mode, so offering it there would be a lie.
    ids = _render(monkeypatch, tmp_path, kobo_support=False, profile=False, new_user=True)
    assert not ids & {'library_mode_monolibrary', 'library_mode_personal'}
