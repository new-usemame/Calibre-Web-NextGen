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
