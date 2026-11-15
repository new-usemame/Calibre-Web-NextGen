# SPDX-License-Identifier: GPL-3.0-or-later
"""Populated v1 upgrades preserve provenance, receipts and owned submission."""
from sqlalchemy import MetaData, create_engine, inspect, select


def test_populated_v1_marker_upgrade_and_cold_open_preserve_jobs_receipts_and_grants(tmp_path):
    from cps.services.acquisition import migration, runtime, storage, secrets
    engine = create_engine('sqlite:///' + str(tmp_path/'app.db'))
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE user (id INTEGER PRIMARY KEY, role INTEGER)')
        conn.exec_driver_sql('INSERT INTO user VALUES (1,65536),(2,0)')
        conn.exec_driver_sql('CREATE TABLE settings (config_acquisition_enabled INTEGER)')
        conn.exec_driver_sql('INSERT INTO settings VALUES (0)')
    migration.migrate_acquisition_schema(engine)
    tables = storage.define_tables(MetaData())
    key = secrets.load_or_create_key(tmp_path/'acquisition.key')
    repo = storage.Repository(engine,tables,secrets.SecretBox(key))
    client = repo.create_connection('Client','sabnzbd',{},enabled=True)
    source = repo.create_connection('Source','newznab',{},enabled=True)
    payload = {'kind':'acquisition','transport':'nzb','media_type':'application/x-nzb',
        'client_id':client.id,'client_revision':1,'release_key':'c'*64}
    job = repo.create_job(1,repo.create_offer(1,source.id,payload),'first',requires_approval=False)
    claim = repo.claim()
    repo.begin_submission(job.id,claim.token)
    repo.record_external(job.id,claim.token,'owned')
    repo.advance(job.id,claim.token,'queued','resolving')
    repo.advance(job.id,claim.token,'resolving','downloading')
    repo.advance(job.id,claim.token,'downloading','staged',source_sha256='a'*64,staging_key='staged-original')
    permit = repo.prepare_publication(job.id,claim.token,'p'*48)
    repo.finalize_import(job.id,permit.token,storage.ImportOutcome('a'*64,'b'*64,(7,),'imported'),
        staging_key=permit.staging_key,finalize_membership=lambda conn,job,outcome:None)
    other = repo.create_job(2,repo.create_offer(2,source.id,payload),'other',requires_approval=False)
    with engine.begin() as conn:
        # Strip only additive bundle fields to represent the genuine prior
        # table shape, including its inline UNIQUE and receipt foreign key.
        conn.exec_driver_sql('DROP TABLE IF EXISTS acquisition_bundle_manifest')
        existing = {c['name'] for c in inspect(conn).get_columns('acquisition_job')}
        for name in ('download_release_key','bundle_parent_id','selected_artifact_id'):
            if name in existing: conn.exec_driver_sql(f'ALTER TABLE acquisition_job DROP COLUMN {name}')
        marker = dict(conn.exec_driver_sql('SELECT * FROM acquisition_schema_migration').mappings().one())
        jobs = [dict(x) for x in conn.exec_driver_sql('SELECT * FROM acquisition_job ORDER BY id').mappings()]
        receipt = dict(conn.exec_driver_sql('SELECT * FROM acquisition_import_receipt').mappings().one())
    for _ in range(2): migration.migrate_acquisition_schema(engine)
    with engine.connect() as conn:
        assert 'acquisition_bundle_manifest' in inspect(conn).get_table_names()
        after = [dict(x) for x in conn.exec_driver_sql('SELECT * FROM acquisition_job ORDER BY id').mappings()]
        for original, current in zip(jobs,after):
            assert all(current[k]==v for k,v in original.items())
            assert current['download_release_key'] is None and current['selected_artifact_id'] is None
        assert dict(conn.exec_driver_sql('SELECT * FROM acquisition_import_receipt').mappings().one())==receipt
        assert dict(conn.exec_driver_sql('SELECT * FROM acquisition_schema_migration').mappings().one())==marker
        assert conn.exec_driver_sql('SELECT role FROM user ORDER BY id').all()==[(65536,),(0,)]
        assert conn.exec_driver_sql('SELECT config_acquisition_enabled FROM settings').scalar()==0
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
    engine.dispose()
    with runtime.open_repository(tmp_path/'app.db') as cold:
        assert cold.get_job(1,job.id).state=='imported'
        assert cold.get_receipt(1,job.id).book_ids==(7,)
        claim = cold.claim()
        assert claim.job.id==other.id
        assert cold.begin_submission(other.id,claim.token) is False
        assert cold.submission_identity(other.id,claim.token)[0]=='owned'
