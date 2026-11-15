"""Built-in "Recently Added" / "Recent Publications" shelves keep moving (#2370).

The templates used to store a calendar date computed when the server started,
so every shelf created from them stopped advancing. These tests evaluate the
template at a later clock, and run the one-shot upgrade against real rows.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import magic_shelf, ub


TEMPLATES = (('recently_added', 'timestamp', 30), ('recent_publications', 'pubdate', 730))


@pytest.mark.parametrize("key,field,days", TEMPLATES)
def test_template_window_follows_the_clock(monkeypatch, key, field, days):
    later = datetime.now(timezone.utc) + timedelta(days=100)

    class LaterDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return later if tz else later.replace(tzinfo=None)

    monkeypatch.setattr(magic_shelf, "datetime", LaterDatetime)
    rule = magic_shelf.SYSTEM_SHELF_TEMPLATES[key]['rules']['rules'][0]
    assert rule['field'] == field
    expr = magic_shelf.build_filter_from_rule(rule)
    assert expr is not None
    (threshold,) = expr.compile().params.values()
    assert isinstance(threshold, datetime)
    age = later.replace(tzinfo=None) - threshold
    assert timedelta(days=days) - timedelta(minutes=1) <= age <= timedelta(days=days)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine, tables=[
        ub.MagicShelf.__table__, ub.MagicShelfRelativeDateMigration.__table__,
    ])
    db_session = sessionmaker(bind=engine)()
    yield db_session
    db_session.close()
    engine.dispose()


CREATED = datetime(2026, 9, 1, 12, 0, 0)


def frozen_rules(field, value):
    return {'condition': 'AND', 'rules': [{
        'id': field, 'field': field, 'type': 'date', 'input': 'text',
        'operator': 'greater', 'value': value,
    }]}


def rolling_rules(field, days):
    return {'condition': 'AND', 'rules': [{
        'id': field, 'field': field, 'type': 'datetime', 'input': 'text',
        'operator': 'in_last_days', 'value': str(days),
    }]}


def add_shelf(session, name, rules, *, is_system=True, user_id=1,
              created=CREATED, modified=None):
    shelf = ub.MagicShelf(user_id=user_id, name=name, rules=rules, is_system=is_system,
                          created=created, last_modified=modified or created)
    session.add(shelf)
    session.commit()
    return shelf.id


def rules_of(session, shelf_id):
    session.expire_all()
    return session.get(ub.MagicShelf, shelf_id).rules


def cutoff(days, *, extra=0):
    return (CREATED - timedelta(days=days + extra)).date().isoformat()


@pytest.mark.parametrize("name,field,days", [
    ('Recently Added', 'timestamp', 30), ('Recent Publications', 'pubdate', 730),
])
def test_startup_created_default_becomes_rolling(session, name, field, days):
    shelf_id = add_shelf(session, name, frozen_rules(field, cutoff(days)))
    ub.migrate_magic_shelf_relative_dates(session)
    assert rules_of(session, shelf_id) == rolling_rules(field, days)


def test_default_saved_unchanged_in_classic_editor_becomes_rolling(session):
    saved = frozen_rules('timestamp', cutoff(30))
    saved['valid'] = True
    saved['rules'][0]['type'] = 'datetime'
    shelf_id = add_shelf(session, 'Recently Added', saved,
                         modified=CREATED + timedelta(days=9))
    ub.migrate_magic_shelf_relative_dates(session)
    assert rules_of(session, shelf_id) == rolling_rules('timestamp', 30)


def test_untouched_shelf_of_account_added_after_startup_becomes_rolling(session):
    # The server had been up five days when this account was created, so its
    # frozen date is five days older than created - 30.
    shelf_id = add_shelf(session, 'Recently Added',
                         frozen_rules('timestamp', cutoff(30, extra=5)),
                         modified=CREATED + timedelta(milliseconds=3))
    ub.migrate_magic_shelf_relative_dates(session)
    assert rules_of(session, shelf_id) == rolling_rules('timestamp', 30)


@pytest.mark.parametrize("value,modified", [
    # The owner typed an older date and saved: the row was modified later.
    (cutoff(30, extra=60), CREATED + timedelta(days=2)),
    # A date after the creation cutoff can never be a frozen default.
    (cutoff(30, extra=-3), CREATED),
    # Unknown modification time: an older date cannot be attributed.
    (cutoff(30, extra=5), None),
])
def test_owner_chosen_dates_are_kept(session, value, modified):
    shelf_id = add_shelf(session, 'Recently Added', frozen_rules('timestamp', value))
    if modified is None:
        session.get(ub.MagicShelf, shelf_id).last_modified = None
        session.commit()
    elif modified != CREATED:
        session.get(ub.MagicShelf, shelf_id).last_modified = modified
        session.commit()
    before = rules_of(session, shelf_id)
    ub.migrate_magic_shelf_relative_dates(session)
    assert rules_of(session, shelf_id) == before


def test_extended_kobo_synced_and_user_shelves_are_kept(session):
    extended = frozen_rules('timestamp', cutoff(30))
    extended['rules'].append({'id': 'title', 'field': 'title', 'type': 'string',
                              'input': 'text', 'operator': 'contains', 'value': 'x'})
    extended_id = add_shelf(session, 'Recently Added', extended)
    own_id = add_shelf(session, 'Recently Added', frozen_rules('timestamp', cutoff(30)),
                       is_system=False, user_id=2)
    garbage_id = add_shelf(session, 'Recent Publications', {'rules': 'nope'}, user_id=3)
    # Narrowing a Kobo-synced shelf would archive books off the device.
    kobo_id = add_shelf(session, 'Recent Publications', frozen_rules('pubdate', cutoff(730)),
                        user_id=4)
    session.get(ub.MagicShelf, kobo_id).kobo_sync = True
    session.commit()
    kobo_rules = rules_of(session, kobo_id)
    ub.migrate_magic_shelf_relative_dates(session)
    assert rules_of(session, kobo_id) == kobo_rules
    assert rules_of(session, extended_id) == extended
    assert rules_of(session, own_id) == frozen_rules('timestamp', cutoff(30))
    assert rules_of(session, garbage_id) == {'rules': 'nope'}


def test_upgrade_runs_once(session):
    ub.migrate_magic_shelf_relative_dates(session)
    # A shelf that later looks frozen (for example restored by hand) is the
    # owner's business after the one-shot upgrade has completed.
    shelf_id = add_shelf(session, 'Recently Added', frozen_rules('timestamp', cutoff(30)))
    ub.migrate_magic_shelf_relative_dates(session)
    assert rules_of(session, shelf_id) == frozen_rules('timestamp', cutoff(30))
    assert session.query(ub.MagicShelfRelativeDateMigration).count() == 1
