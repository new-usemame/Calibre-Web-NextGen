"""Password hints must render, and the policy sent to the SPA must be enforced."""
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import render_template
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from werkzeug.security import check_password_hash

from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit


class HintParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_hint = False
        self.hint = []

    def handle_starttag(self, tag, attrs):
        if dict(attrs).get('id') == 'password-rules-hint':
            self.in_hint = True

    def handle_endtag(self, tag):
        if tag == 'p':
            self.in_hint = False

    def handle_data(self, text):
        if self.in_hint:
            self.hint.append(text)


@pytest.mark.parametrize('minimum, enabled, number, expected', [
    (1, True, False, 'Password requirements: at least 1 character.'),
    (10, True, True, 'Password requirements: at least 10 characters, including a number.'),
    (0, True, True, 'Password requirements: including a number.'),
    (10, False, True, ''),
    (0, True, False, ''),
])
def test_classic_user_form_renders_active_password_rules(monkeypatch, tmp_path, minimum, enabled, number, expected):
    """Render the shipping form through Babel's real Jinja translation wrapper."""
    from cps import config, constants
    world = LibraryWorld(monkeypatch, tmp_path)
    try:
        user = world.add_user('alice')
        user.role |= constants.ROLE_PASSWD
        for name, value in dict(policy=enabled, min_length=minimum, number=number,
                                lower=False, upper=False, character=False, special=False).items():
            monkeypatch.setattr(config, 'config_password_' + name, value, raising=False)
        # Only the surrounding layout is replaced; the entire user form and
        # Flask-Babel's gettext/ngettext functions execute unchanged.
        world.app.jinja_loader = ChoiceLoader([
            DictLoader({'layout.html': '{% block body %}{% endblock %}'}),
            FileSystemLoader(Path(__file__).resolve().parents[2] / 'cps/templates'),
        ])
        with world.app.test_request_context('/admin/newuser'):
            html = render_template('user_edit.html', content=user, current_user=user,
                                   config=defaultdict(lambda: False, vars(config)),
                                   new_user=True, profile=False,
                                   url_for=lambda *args, **kwargs: '/unused',
                                   csrf_token=lambda: 'test', registered_oauth={},
                                   opds_root_labels={}, magic_shelf_order_labels={})
        parser = HintParser()
        parser.feed(html)
        assert ' '.join(''.join(parser.hint).split()) == expected
    finally:
        world.close()


@pytest.mark.parametrize('setting, flag, invalid', [
    ('min_length', 'min_length', 'Aa1!'),
    ('number', 'number', 'LettersOnly!'),
    ('lower', 'lower', 'UPPERCASE1!'),
    ('upper', 'upper', 'lowercase1!'),
    ('character', 'letter', '123456789!'),
    ('special', 'special', 'NoSpecial123'),
])
def test_account_policy_matches_password_change_validation(monkeypatch, tmp_path, setting, flag, invalid):
    """Drive real account GET/POST and persisted hashes, with each rule enabled alone."""
    from cps import config, constants
    world = LibraryWorld(monkeypatch, tmp_path)
    world.enable_web()
    try:
        user = world.add_user('alice')
        user.role |= constants.ROLE_PASSWD
        world.session.commit()
        for name, value in dict(policy=True, min_length=0, number=False, lower=False,
                                upper=False, character=False, special=False).items():
            monkeypatch.setattr(config, 'config_password_' + name, value, raising=False)
        monkeypatch.setattr(config, 'config_password_' + setting, 10 if setting == 'min_length' else True)
        browser = world.browser('alice')
        response = browser.get('/api/v1/account')
        assert response.status_code == 200
        policy = response.get_json()['password_policy']
        assert policy['enabled'] is True
        assert policy[flag] == (10 if setting == 'min_length' else True)
        rejected = browser.post('/api/v1/account/password', json={
            'current_password': 'secret', 'new_password': invalid,
        })
        assert rejected.status_code == 400
        assert rejected.get_json()['error']['code'] == 'invalid_request'
        assert check_password_hash(user.password, 'secret')
        accepted = browser.post('/api/v1/account/password', json={
            'current_password': 'secret', 'new_password': 'ValidPassword123!',
        })
        assert accepted.status_code == 204
        assert check_password_hash(user.password, 'ValidPassword123!')
        monkeypatch.setattr(config, 'config_password_policy', False)
        assert browser.get('/api/v1/account').get_json()['password_policy']['enabled'] is False
        assert browser.post('/api/v1/account/password', json={
            'current_password': 'ValidPassword123!', 'new_password': invalid,
        }).status_code == 204
        assert check_password_hash(user.password, invalid)
    finally:
        world.close()
