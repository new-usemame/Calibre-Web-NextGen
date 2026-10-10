"""#1734: manage hidden smart shelves through classic per-user preferences.

The overview must offer restoration while navigation respects hiding. Writes
persist preferences, refuse inaccessible shelves, and reject malformed bodies.
"""
import inspect
from types import SimpleNamespace
import flask
from flask_babel import Babel
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture()
def management(monkeypatch):
    from cps import ub, magic_shelf
    from cps.api import magicshelves as api
    engine = create_engine('sqlite://')
    for model in (ub.MagicShelf, ub.HiddenMagicShelfTemplate):
        model.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        ub.MagicShelf(id=1, user_id=7, name='Recently Added', is_system=True, is_public=0),
        ub.MagicShelf(id=2, user_id=9, name='Shared', is_system=False, is_public=1),
        ub.MagicShelf(id=3, user_id=9, name='Private', is_system=False, is_public=0),
        ub.MagicShelf(id=4, user_id=7, name='My own', is_system=False, is_public=0),
    ])
    session.add(ub.HiddenMagicShelfTemplate(user_id=7, template_key='recently_added'))
    session.commit()
    user = SimpleNamespace(id=7, is_authenticated=True, role_admin=lambda: False,
                           role_edit_shelfs=lambda: False, opds_only_shelves_sync=False)
    monkeypatch.setattr(ub, 'session', session)
    monkeypatch.setattr(api, 'current_user', user)
    app = flask.Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='test')
    Babel(app)
    app.add_url_rule('/api/v1/magicshelves', view_func=inspect.unwrap(api.list_magic_shelves))
    handler = getattr(api, 'set_magic_shelf_visibility', None)
    if handler:
        app.add_url_rule('/api/v1/magicshelves/<int:shelf_id>/visibility',
                         view_func=inspect.unwrap(handler), methods=['POST'])
    yield app.test_client(), session, ub, magic_shelf
    session.close()
    engine.dispose()

@pytest.mark.unit
def test_management_overview_includes_hidden_but_never_other_private_shelves(management):
    client, _, _, _ = management
    items = {s['id']: s for s in client.get('/api/v1/magicshelves?manage=1').json['items']}
    assert set(items) == {1, 2, 4}
    assert items[1]['is_hidden'] is True
    assert items[1]['can_hide'] is True
    assert items[4]['can_hide'] is False
    assert {s['id'] for s in client.get('/api/v1/magicshelves').json['items']} == {2, 4}

@pytest.mark.unit
def test_restore_and_hide_persist_classic_preferences_idempotently(management):
    client, session, ub, magic = management
    for _ in range(2):
        assert client.post('/api/v1/magicshelves/1/visibility', json={'visible': True}).status_code == 200
    assert {s.id for s in magic.get_visible_magic_shelves_for_user(7)} == {1, 2, 4}
    for _ in range(2):
        assert client.post('/api/v1/magicshelves/2/visibility', json={'visible': False}).status_code == 200
    assert {s.id for s in magic.get_visible_magic_shelves_for_user(7)} == {1, 4}
    assert session.query(ub.HiddenMagicShelfTemplate).filter_by(user_id=7, shelf_id=2).count() == 1

@pytest.mark.unit
def test_visibility_refuses_inaccessible_shelves_and_non_boolean_payloads(management):
    client, session, ub, _ = management
    before = session.query(ub.HiddenMagicShelfTemplate).count()
    assert client.post('/api/v1/magicshelves/3/visibility', json={'visible': False}).status_code == 403
    assert client.post('/api/v1/magicshelves/4/visibility', json={'visible': False}).status_code == 400
    for payload in ({'visible': 'false'}, [], None, {'visible': 0}):
        assert client.post('/api/v1/magicshelves/2/visibility', json=payload).status_code == 400
    assert session.query(ub.HiddenMagicShelfTemplate).count() == before

@pytest.mark.unit
def test_ordinary_shelf_opds_selection_is_per_viewer_and_partial_saves_preserve_it(monkeypatch):
    from cps import ub, shelf as core
    from cps.api import shelves as api
    engine = create_engine('sqlite://')
    for model in (ub.Shelf, ub.OpdsShelfExposure):
        model.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    user = SimpleNamespace(id=7, is_authenticated=True, role_edit_shelfs=lambda: False,
                           role_share_shelfs=lambda: True, opds_only_shelves_sync=True)
    monkeypatch.setattr(ub, 'session', session)
    monkeypatch.setattr(api, 'current_user', user)
    monkeypatch.setattr(core, 'current_user', user)
    monkeypatch.setattr(api, '_shelf_book_count', lambda *_: 0)
    app = flask.Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='test')
    app.add_url_rule('/shelves', view_func=inspect.unwrap(api.create_shelf_api), methods=['POST'])
    app.add_url_rule('/shelves/<int:shelf_id>', view_func=inspect.unwrap(api.update_shelf_api), methods=['POST'])
    client = app.test_client()
    try:
        created = client.post('/shelves', json={'name': 'Feed choice', 'is_public': True, 'opds_expose': True})
        assert created.status_code == 201
        sid = created.json['id']
        assert ub.is_opds_shelf_exposed_for_user(7, sid)
        assert not ub.is_opds_shelf_exposed_for_user(9, sid)
        assert client.post(f'/shelves/{sid}', json={'name': 'Renamed'}).status_code == 200
        assert ub.is_opds_shelf_exposed_for_user(7, sid)
        assert client.post(f'/shelves/{sid}', json={'opds_expose': False}).status_code == 200
        assert not ub.is_opds_shelf_exposed_for_user(7, sid)
        user.id = 9
        user.role_edit_shelfs = lambda: True
        before = session.query(ub.Shelf).get(sid).kobo_sync
        assert client.post(f'/shelves/{sid}', json={'kobo_sync': True}).status_code == 403
        assert session.query(ub.Shelf).get(sid).kobo_sync == before
    finally:
        session.close()
        engine.dispose()

@pytest.mark.unit
@pytest.mark.parametrize('rules', [
    {'condition': 'OR', 'rules': [{'condition': 'AND', 'rules': [
        {'id': 'future_field', 'operator': 'contains', 'value': {'toString': None}},
    ]}]},
    {'condition': 'AND', 'rules': [
        {'id': 'title', 'operator': 'retired_operator', 'value': 0},
    ]},
    {'condition': 'AND', 'rules': []},
])
def test_shelf_with_no_evaluable_filter_keeps_rules_for_editor(management, monkeypatch, rules):
    from cps.api import magicshelves as api
    client, session, ub, _ = management
    shelf = session.query(ub.MagicShelf).get(4)
    shelf.rules = rules
    session.commit()
    monkeypatch.setattr(api, 'load_configured_columns', lambda _: [])
    monkeypatch.setattr(api.config, 'config_books_per_page', 24, raising=False)
    client.application.add_url_rule('/api/v1/magicshelf/<int:shelf_id>',
                                    view_func=inspect.unwrap(api.magic_shelf_books))
    response = client.get('/api/v1/magicshelf/4')
    assert response.status_code == 200
    assert response.json['items'] == []
    assert response.json['total'] == 0
    assert response.json.get('rules') == rules
    assert response.json['can_edit'] is True
    # Returning rules must retain the existing private-shelf authorization gate.
    assert client.get('/api/v1/magicshelf/3').status_code == 403
