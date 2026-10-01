# SPDX-License-Identifier: GPL-3.0-or-later
"""Real populated app.db upgrades must not confuse Store with Global Library."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from cps import config_sql, constants, ub

pytestmark = pytest.mark.unit
ACCESS, AUTO = 1 << 11, 1 << 12
LEGACY_ACCESS, LEGACY_AUTO = 1 << 9, 1 << 10
STORE_DDL = (
    "CREATE TABLE store_credentials (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, provider TEXT NOT NULL, ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, key_version INTEGER NOT NULL)",
    "CREATE TABLE store_request_mappings (id INTEGER PRIMARY KEY, shelfmark_request_id TEXT NOT NULL, user_id INTEGER NOT NULL, work JSON NOT NULL, release JSON NOT NULL)",
    "CREATE TABLE store_download_mappings (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL, format TEXT NOT NULL)",
)


def _fixture(path, layout):
    engine = create_engine(f"sqlite:///{path}")
    ub.Base.metadata.create_all(engine)
    config_sql._Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table in ('acquisition_schema_migration', 'acquisition_import_receipt', 'acquisition_job', 'acquisition_offer', 'acquisition_connection'):
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}"')
    session = sessionmaker(bind=engine)()
    masks = [1 | 2 | LEGACY_ACCESS | LEGACY_AUTO, 2 | LEGACY_ACCESS, 16, 1 << 14]
    users = [ub.User(id=i+1, name=f"upgrade-{i}", email=f"upgrade-{i}@example.invalid", role=mask) for i,mask in enumerate(masks)]
    session.add_all(users)
    session.add(config_sql._Settings(id=1, config_default_role=LEGACY_ACCESS|16))
    session.add(ub.OAuthProvider(id=1, provider_name="fixture", oauth_default_role=LEGACY_AUTO|2))
    session.add(ub.OAuthProvider(id=2, provider_name="null-template", oauth_default_role=None))
    session.commit(); session.close()
    with engine.begin() as conn:
        conn.execute(ub.Annotation.__table__.insert().values(user_id=2, book_id=77,
            annotation_id="preserved", source="kobo", highlighted_text="unchanged passage",
            note_text="unchanged note", server_modified_at=datetime(2026,1,1),
            client_modified_at=datetime(2026,1,1)))
        conn.exec_driver_sql('UPDATE "oauthProvider" SET oauth_default_role=NULL WHERE id=2')
    if layout in ('legacy_store', 'pre_personal', 'partial_store'):
        ub.rollback_user_library_schema(engine)
        # The supported personal-library downgrade clears bit9. A real Store
        # database uses that bit for Store access, so restore its original masks.
        with engine.begin() as conn:
            for identity, mask in enumerate(masks, start=1):
                conn.exec_driver_sql('UPDATE user SET role=? WHERE id=?', (mask, identity))
    else:
        with engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO user_library_book(user_id, book_id, added_at) VALUES (2,77,CURRENT_TIMESTAMP)")
    if layout in ('legacy_store', 'hybrid', 'partial_store'):
        with engine.begin() as conn:
            for sql in STORE_DDL[:1] if layout == 'partial_store' else STORE_DDL:
                conn.exec_driver_sql(sql)
            conn.exec_driver_sql("INSERT INTO store_credentials VALUES (1,2,'legacy',X'001122',X'445566',1)")
            if layout != 'partial_store':
                conn.exec_driver_sql("INSERT INTO store_request_mappings VALUES (1,'queued-legacy',2,'{}','{}')")
                conn.exec_driver_sql("INSERT INTO store_download_mappings VALUES (1,2,'legacy','owned','History','EPUB')")
    return engine, masks


def _snapshot(engine, tables):
    with engine.connect() as conn:
        return {name: conn.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1').all() for name in tables}


def _roles(engine):
    with engine.connect() as conn:
        return list(conn.exec_driver_sql('SELECT role FROM user ORDER BY id').scalars())


def test_boot_legacy_store_remaps_grants_before_personal_schema_erases_provenance(tmp_path, monkeypatch):
    path = tmp_path/'app.db'
    engine, masks = _fixture(path, 'legacy_store')
    preserved = _snapshot(engine, ['annotation','store_credentials','store_request_mappings','store_download_mappings'])
    previous_session, previous_path = ub.session, ub.app_DB_path
    monkeypatch.setattr(constants, 'CONFIG_DIR', str(tmp_path/'config'))
    try:
        for _ in range(2):
            ub.init_db(str(path))
            ub.session.close();ub.session.bind.dispose()
        expected = [(m & ~(LEGACY_ACCESS|LEGACY_AUTO)) | (ACCESS if m & LEGACY_ACCESS else 0) | (AUTO if m & LEGACY_AUTO else 0) for m in masks]
        assert not (_roles(engine)[1] & constants.ROLE_BROWSE_GLOBAL)
        assert _roles(engine) == expected
        assert _snapshot(engine, preserved) == preserved
        with engine.connect() as conn:
            assert conn.exec_driver_sql('SELECT config_default_role FROM settings').scalar_one() == ACCESS|16
            assert conn.exec_driver_sql('SELECT oauth_default_role FROM "oauthProvider" ORDER BY id').all() == [(AUTO|2,), (None,)]
            marker=conn.exec_driver_sql('SELECT * FROM acquisition_schema_migration').mappings().one()
            assert marker['source_layout'] == 'legacy_store'
            assert marker['status'] == 'mapped'
            assert conn.exec_driver_sql('SELECT config_acquisition_enabled FROM settings').scalar_one() == 0
    finally:
        ub.session,ub.app_DB_path=previous_session,previous_path
        engine.dispose()


@pytest.mark.parametrize('layout, expected_layout, status', [
    ('audit','audit','preserved'), ('hybrid','hybrid','needs_review'),
    ('partial_store','hybrid','needs_review'), ('pre_personal','pre_personal','preserved'),
])
def test_populated_direct_upgrade_twice_preserves_current_and_ambiguous_masks(
        tmp_path, monkeypatch, layout, expected_layout, status):
    path=tmp_path/'app.db'
    engine,masks=_fixture(path,layout)
    names=inspect(engine).get_table_names()
    kept=['user','annotation','oauthProvider']
    kept += [t for t in ('user_library_book','store_credentials','store_request_mappings','store_download_mappings') if t in names]
    before=_snapshot(engine,kept)
    monkeypatch.setattr(constants,'CONFIG_DIR',str(tmp_path/'config'))
    session=sessionmaker(bind=engine)()
    monkeypatch.setattr(ub,'session',session)
    try:
        # Exercise the real direct migration entry point as well as cold init.
        ub.migrate_Database(session)
        assert _roles(engine)==masks
        assert _snapshot(engine,[t for t in kept if t!='user'])=={t:before[t] for t in kept if t!='user'}
        with engine.connect() as conn:
            marker=dict(conn.exec_driver_sql('SELECT * FROM acquisition_schema_migration').mappings().one())
            assert (marker['source_layout'],marker['status'])==(expected_layout,status)
            assert all(r['before']==r['after'] for r in json.loads(marker['role_changes_json']))
            assert conn.exec_driver_sql('SELECT config_acquisition_enabled FROM settings').scalar_one()==0
        # Later explicit permissions/settings changes must not be replayed away.
        session.query(ub.User).filter_by(id=3).update({'role': ACCESS|16})
        session.commit()
        with engine.begin() as conn:
            conn.exec_driver_sql('UPDATE settings SET config_acquisition_enabled=1')
        after=_snapshot(engine,kept+['acquisition_schema_migration'])
        ub.migrate_Database(session)
        assert _snapshot(engine,after)==after
        with engine.connect() as conn:
            assert conn.exec_driver_sql('SELECT config_acquisition_enabled FROM settings').scalar_one()==1
    finally:
        session.close();engine.dispose()


def test_new_admin_regular_and_oauth_templates_have_no_automatic_acquisition_grants(tmp_path,monkeypatch):
    path=tmp_path/'fresh.db'
    previous_session,previous_path=ub.session,ub.app_DB_path
    monkeypatch.setattr(constants,'CONFIG_DIR',str(tmp_path/'config'))
    try:
        ub.init_db(str(path))
        session=ub.session
        admin=session.query(ub.User).filter(ub.User.role.op('&')(constants.ROLE_ADMIN)!=0).one()
        assert admin.role==991  # established pre-acquisition admin permissions
        assert admin.role_browse_global()
        assert not admin.role_acquisition_access() and not admin.role_acquisition_auto_approve()
        regular=ub.User(name='regular',email='regular@example.invalid')
        provider=ub.OAuthProvider(provider_name='fresh')
        session.add_all([regular,provider]);session.commit()
        assert regular.role==constants.ROLE_USER==0
        assert provider.oauth_default_role is None
        config_sql._Base.metadata.create_all(session.bind)
        settings=config_sql._Settings()
        session.add(settings);session.commit()
        assert settings.config_default_role==0
        assert settings.config_acquisition_enabled is False
        # New admin-account constructors use this exact shared mask.
        second_admin=ub.User(name='second-admin',email='admin2@example.invalid',role=constants.ADMIN_USER_ROLES)
        session.add(second_admin);session.commit()
        assert not second_admin.role_acquisition_access() and not second_admin.role_acquisition_auto_approve()
        with session.bind.connect() as conn:
            assert conn.exec_driver_sql('SELECT source_layout FROM acquisition_schema_migration').scalar_one()=='fresh'
            for table in ('acquisition_connection','acquisition_offer','acquisition_job','acquisition_import_receipt'):
                assert conn.exec_driver_sql(f'SELECT COUNT(*) FROM {table}').scalar_one()==0
        assert not (tmp_path/'acquisition.key').exists()
    finally:
        if ub.session is not previous_session:
            ub.session.close();ub.session.bind.dispose()
        ub.session,ub.app_DB_path=previous_session,previous_path


def test_role_remap_and_marker_rollback_together_then_retry_once(tmp_path):
    from cps.services.acquisition.migration import migrate_acquisition_schema
    engine,masks=_fixture(tmp_path/'app.db','legacy_store')
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TRIGGER refuse_template BEFORE UPDATE OF config_default_role ON settings BEGIN SELECT RAISE(ABORT,'fixture-write-failure'); END")
    before=_snapshot(engine,['user','settings','oauthProvider','store_credentials'])
    try:
        with pytest.raises(Exception,match='fixture-write-failure'):
            migrate_acquisition_schema(engine)
        assert _snapshot(engine,before)==before
        assert 'acquisition_schema_migration' not in inspect(engine).get_table_names()
        with engine.begin() as conn:
            conn.exec_driver_sql('DROP TRIGGER refuse_template')
        first=migrate_acquisition_schema(engine)
        second=migrate_acquisition_schema(engine)
        assert first==second
        assert _roles(engine)[1] == ACCESS|2
        snapshots=json.loads(first['role_changes_json'])
        assert next(r for r in snapshots if r['table']=='user' and r['id']==2)['before']==LEGACY_ACCESS|2
    finally:
        engine.dispose()


def test_concurrent_boots_capture_legacy_masks_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from cps.services.acquisition.migration import migrate_acquisition_schema
    engine,masks=_fixture(tmp_path/'app.db','legacy_store')
    barrier=Barrier(2)
    def upgrade():
        barrier.wait()
        return migrate_acquisition_schema(engine)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:upgrade(),range(2)))
        assert results[0]==results[1]
        assert _roles(engine)[0]==1|2|ACCESS|AUTO
        assert len(_snapshot(engine,['acquisition_schema_migration'])['acquisition_schema_migration'])==1
    finally:
        engine.dispose()


def test_settings_row_without_the_acquisition_column_still_writes_and_reads_off(tmp_path):
    """A write that never heard of this feature must still succeed.

    `default=False` is client-side: SQLAlchemy supplies it only when its own
    mapper performs the INSERT. Every other writer -- an older database being
    upgraded, a fixture, any raw ``INSERT INTO settings (...)`` naming only the
    columns it knows about -- reached a NOT NULL column with no value and died
    with "NOT NULL constraint failed: settings.config_acquisition_enabled".

    That is the inverse of shipping switched off: a dormant feature's column was
    breaking writes that have nothing to do with the feature. Four unrelated
    suites (hidden books, custom column sort, checksum table creation) failed on
    it, which is what a schema-level break looks like from the outside.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    config_sql._Settings.__table__.create(engine)

    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO settings (id) VALUES (1)")

    with engine.connect() as conn:
        stored = conn.exec_driver_sql(
            "SELECT config_acquisition_enabled FROM settings WHERE id=1"
        ).scalar()

    assert not stored, (
        "a settings row created without naming the acquisition column must read "
        f"as switched off, got {stored!r}"
    )


def test_fresh_settings_table_carries_the_same_sql_default_the_upgrade_writes(tmp_path):
    """Fresh install and upgrade must not disagree about the schema.

    ``migrate_acquisition_schema`` adds the column with ``DEFAULT 0`` on an
    existing database. If the model omits ``server_default`` then a freshly
    created table has no default at all, so the two supported ways of arriving
    at the current version produce different schemas -- and only one of them
    tolerates a write that omits the column. Pin the DDL property itself, so
    dropping ``server_default`` fails here even if no raw INSERT happens to
    exercise it.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    config_sql._Settings.__table__.create(engine)

    with engine.connect() as conn:
        defaults = {
            row[1]: row[4]
            for row in conn.exec_driver_sql("PRAGMA table_info(settings)")
        }

    assert "config_acquisition_enabled" in defaults, "column missing from a fresh table"
    assert defaults["config_acquisition_enabled"] is not None, (
        "the freshly created settings table gives config_acquisition_enabled no SQL "
        "default, while the upgrade path adds it with DEFAULT 0 -- the two schemas "
        "disagree and only the upgraded one survives an INSERT that omits the column"
    )


def test_unreadable_role_value_leaves_the_app_bootable_and_asks_for_review(tmp_path):
    """A value this code cannot interpret is not a reason to refuse to start.

    Everything else an account owns is unrelated to acquisition, so the
    feature reports itself unavailable instead of taking the whole app down.
    """
    from cps.services.acquisition.migration import migrate_acquisition_schema
    from cps.services.acquisition.admission import instance_state
    database = tmp_path / 'app.db'
    engine, masks = _fixture(database, 'legacy_store')
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("UPDATE user SET role='not-a-mask' WHERE id=3")
        before = _snapshot(engine, ['user', 'settings', 'oauthProvider'])
        result = migrate_acquisition_schema(engine)
        assert result['status'] == 'needs_review'
        assert json.loads(result['role_changes_json']) == []
        # Two-phase: the valid rows read before the bad one stay untouched too.
        assert _snapshot(engine, ['user', 'settings', 'oauthProvider']) == before
        assert 'acquisition_job' in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    assert instance_state(str(database))['migration_status'] == 'needs_review'


def test_negative_role_value_is_reviewed_rather_than_reinterpreted(tmp_path):
    from cps.services.acquisition.migration import migrate_acquisition_schema
    engine, masks = _fixture(tmp_path / 'app.db', 'legacy_store')
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql('UPDATE user SET role=-1 WHERE id=4')
        result = migrate_acquisition_schema(engine)
        assert result['status'] == 'needs_review'
        assert _roles(engine) == masks[:3] + [-1], 'remapped roles despite an unreadable one'
    finally:
        engine.dispose()


def test_marker_from_an_unknown_build_is_reported_not_raised(tmp_path):
    """A newer marker must not stop this build booting, or change any role."""
    from cps.services.acquisition.migration import migrate_acquisition_schema
    engine, masks = _fixture(tmp_path / 'app.db', 'legacy_store')
    try:
        migrate_acquisition_schema(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql('UPDATE acquisition_schema_migration SET version=99')
        before = _roles(engine)
        result = migrate_acquisition_schema(engine)
        assert result['status'] == 'needs_review'
        assert _roles(engine) == before
    finally:
        engine.dispose()


def test_role_template_without_identity_keeps_booting_and_changes_no_role(tmp_path):
    """An ambiguous role table disables acquisition, not the application.

    The id-less table comes after `user` in the scan, so this also pins that
    the user rows already read are not remapped on the way to finding it.
    """
    from cps.services.acquisition.migration import migrate_acquisition_schema
    from cps.services.acquisition.admission import instance_state
    database = tmp_path / 'app.db'
    engine, masks = _fixture(database, 'legacy_store')
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql('DROP TABLE settings')
            conn.exec_driver_sql('CREATE TABLE settings (config_default_role INTEGER)')
            conn.exec_driver_sql(f'INSERT INTO settings VALUES ({LEGACY_ACCESS | 16})')
        result = migrate_acquisition_schema(engine)
        assert result['status'] == 'needs_review'
        assert _roles(engine) == masks, 'remapped user roles before refusing the ambiguous table'
        with engine.connect() as conn:
            assert conn.exec_driver_sql('SELECT config_default_role FROM settings').scalar_one() == LEGACY_ACCESS | 16
            assert conn.exec_driver_sql(
                'SELECT status FROM acquisition_schema_migration').scalar_one() == 'needs_review'
    finally:
        engine.dispose()
    assert instance_state(str(database)) == {'enabled': False, 'migration_status': 'needs_review'}


def test_a_database_from_an_earlier_build_gains_the_importing_bound(tmp_path):
    """The marker makes later boots return early; the new column must not wait on it."""
    from cps.services.acquisition.migration import migrate_acquisition_schema
    engine, _ = _fixture(tmp_path / 'app.db', 'pre_personal')
    try:
        migrate_acquisition_schema(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql('ALTER TABLE acquisition_job DROP COLUMN importing_since')
        migrate_acquisition_schema(engine)
        assert 'importing_since' in {c['name'] for c in inspect(engine).get_columns('acquisition_job')}
    finally:
        engine.dispose()


def test_existing_opds_requests_survive_additive_usenet_upgrade_and_second_boot(tmp_path):
    from cps.services.acquisition.migration import migrate_acquisition_schema
    from cps.services.acquisition.storage import Repository, define_tables
    from cps.services.acquisition.secrets import SecretBox
    from sqlalchemy import MetaData
    engine, _ = _fixture(tmp_path / 'app.db', 'audit')
    try:
        marker = migrate_acquisition_schema(engine)
        tables = define_tables(MetaData())
        repo = Repository(engine, tables, SecretBox(b'x'*32))
        source = repo.create_connection('Old OPDS', 'opds', {'endpoint': 'https://catalog.example/feed'}, enabled=True)
        offer = repo.create_offer(2, source.id, {'title': 'Existing request'})
        job = repo.create_job(2, offer, 'old-request')
        before = _roles(engine)
        with engine.begin() as conn:
            for column in ('client_revision', 'external_id', 'submission_started'):
                conn.exec_driver_sql(f'ALTER TABLE acquisition_job DROP COLUMN {column}')
            conn.exec_driver_sql('ALTER TABLE acquisition_connection DROP COLUMN deleted')
        for _ in range(2):
            assert migrate_acquisition_schema(engine) == marker
            assert repo.get_job(2, job.id).state == 'awaiting_approval'
            assert repo.get_job(2, job.id).title == 'Existing request'
            assert repo.connection_config(source.id).config['endpoint'] == 'https://catalog.example/feed'
            assert _roles(engine) == before
        with engine.connect() as conn:
            assert conn.exec_driver_sql('SELECT client_revision, external_id, submission_started FROM acquisition_job WHERE id=?', (job.id,)).one() == (None,None,None)
    finally:
        engine.dispose()


def test_existing_sab_identity_survives_nullable_attempt_key_upgrade(tmp_path):
    from cps.services.acquisition.migration import migrate_acquisition_schema
    from cps.services.acquisition.storage import Repository, define_tables
    from cps.services.acquisition.secrets import SecretBox
    from sqlalchemy import MetaData
    engine, _ = _fixture(tmp_path / 'app.db', 'audit')
    try:
        marker = migrate_acquisition_schema(engine)
        repo = Repository(engine, define_tables(MetaData()), SecretBox(b'x' * 32))
        client = repo.create_connection('SAB', 'sabnzbd', {}, enabled=True)
        source = repo.create_connection('Indexer', 'newznab', {}, enabled=True)
        offer = repo.create_offer(2, source.id, {'transport': 'nzb', 'client_id': client.id, 'client_revision': 1, 'release_key': 'a' * 64})
        job = repo.create_job(2, offer, 'old-usenet', requires_approval=False)
        claim = repo.claim(); repo.begin_submission(job.id, claim.token)
        repo.record_external(job.id, claim.token, 'existing-SAB-id')
        before = repo.external_status(job.id, claim.token)
        with engine.begin() as conn:
            conn.exec_driver_sql('ALTER TABLE acquisition_job DROP COLUMN submission_key')
            conn.exec_driver_sql('ALTER TABLE acquisition_job DROP COLUMN submission_invalid')
        for _ in range(2):
            assert migrate_acquisition_schema(engine) == marker
            assert repo.submission_identity(job.id, claim.token) == (*before, None)
    finally:
        engine.dispose()
