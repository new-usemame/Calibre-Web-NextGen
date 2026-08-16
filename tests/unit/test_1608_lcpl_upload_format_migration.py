# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Existing installs must receive LCPL upload support without losing intent."""

import inspect

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.unit


def _config(formats, *, migrated=False):
    from cps.config_sql import ConfigSQL

    config = ConfigSQL()
    config.config_upload_formats = formats
    config.config_upload_formats_lcpl_migrated = migrated
    saves = []

    def _save():
        saves.append(
            (
                config.config_upload_formats,
                config.config_upload_formats_lcpl_migrated,
            )
        )

    config.save = _save
    return config, saves


def test_existing_allowlist_appends_lcpl_once_without_reordering():
    config, saves = _config("mobi,epub,pdf,acsm")

    config.reconcile_lcpl_upload_format()

    assert config.config_upload_formats == "mobi,epub,pdf,acsm,lcpl"
    assert config.config_upload_formats_lcpl_migrated is True
    assert saves == [("mobi,epub,pdf,acsm,lcpl", True)]


def test_allowlist_already_containing_lcpl_is_untouched_but_marked():
    original = "epub,lcpl,pdf"
    config, saves = _config(original)

    config.reconcile_lcpl_upload_format()

    assert config.config_upload_formats == original
    assert config.config_upload_formats_lcpl_migrated is True
    assert saves == [(original, True)]


def test_running_migration_twice_does_not_append_twice():
    config, saves = _config("epub,pdf")

    config.reconcile_lcpl_upload_format()
    config.reconcile_lcpl_upload_format()

    assert config.config_upload_formats == "epub,pdf,lcpl"
    assert saves == [("epub,pdf,lcpl", True)]


def test_user_removal_after_marker_is_never_reversed():
    config, saves = _config("epub,pdf")
    config.reconcile_lcpl_upload_format()
    config.config_upload_formats = "epub,pdf"

    config.reconcile_lcpl_upload_format()

    assert config.config_upload_formats == "epub,pdf"
    assert saves == [("epub,pdf,lcpl", True)]


def test_deliberately_trimmed_allowlist_loses_nothing():
    config, saves = _config("epub,pdf")

    config.reconcile_lcpl_upload_format()

    assert config.config_upload_formats.split(',') == ["epub", "pdf", "lcpl"]
    assert saves == [("epub,pdf,lcpl", True)]


def test_append_uses_admin_form_normalization_without_losing_empty_entries():
    config, saves = _config(" EPUB ,,pdf,EPUB")

    config.reconcile_lcpl_upload_format()

    assert config.config_upload_formats == "epub,,pdf,lcpl"
    assert saves == [("epub,,pdf,lcpl", True)]


def test_empty_allow_all_sentinel_is_marked_without_being_narrowed():
    config, saves = _config("")

    config.reconcile_lcpl_upload_format()

    assert config.config_upload_formats == ""
    assert saves == [("", True)]


def test_fresh_install_default_contains_every_upload_format(tmp_path):
    from cps import config_sql, constants

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        config_sql.load_configuration(session, Fernet.generate_key())
        stored = session.query(config_sql._Settings).one().config_upload_formats
        assert set(stored.split(',')) == constants.EXTENSIONS_UPLOAD
        assert 'lcpl' in stored.split(',')
    finally:
        session.close()
        engine.dispose()


def test_marker_and_later_user_removal_persist_across_restart(tmp_path):
    from cps import config_sql

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    Session = sessionmaker(bind=engine)
    key = Fernet.generate_key()
    session = Session()
    config_sql.load_configuration(session, key)
    stored = session.query(config_sql._Settings).one()
    stored.config_upload_formats = "mobi,epub,pdf"
    stored.config_upload_formats_lcpl_migrated = False
    session.commit()

    config = config_sql.ConfigSQL()
    config.init_config(session, key, None)
    session.expire_all()
    stored = session.query(config_sql._Settings).one()
    assert stored.config_upload_formats == "mobi,epub,pdf,lcpl"
    assert stored.config_upload_formats_lcpl_migrated is True

    config.config_upload_formats = "mobi,epub,pdf"
    config.save()
    session.close()

    restarted_session = Session()
    restarted = config_sql.ConfigSQL()
    restarted.init_config(restarted_session, key, None)
    restarted_session.expire_all()
    final = restarted_session.query(config_sql._Settings).one()
    assert final.config_upload_formats == "mobi,epub,pdf"
    assert final.config_upload_formats_lcpl_migrated is True
    restarted_session.close()
    engine.dispose()


def test_reconciliation_runs_immediately_after_config_load():
    from cps.config_sql import ConfigSQL

    source = inspect.getsource(ConfigSQL.init_config)
    assert source.index("self.load()") < source.index(
        "self.reconcile_lcpl_upload_format()"
    )
