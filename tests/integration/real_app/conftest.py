"""Real application fixture. Run in a fresh interpreter; cps owns process state."""
import sqlite3
import tempfile
from pathlib import Path
import sys

import pytest


def _boot_real_app(tmp_path_factory, *, kobo_sync):
    """Boot one real application in this interpreter and yield it.

    ``kobo_sync`` is written to the stored settings *before* the application
    factory runs, because ``cps/main.py:124`` registers the ``kobo``,
    ``kobo_auth``, ``readingservices_api_v3`` and ``readingservices_userstorage``
    blueprints only when ``config.config_kobo_sync`` is true at registration
    time (``cps/kobo.py:1164``). A fixture booted with the shipped defaults
    therefore has no ``/api/v3/content/<id>/annotations`` rule at all, and an
    authenticated Kobo request against it answers 404 no matter what state the
    databases hold. ``blueprints.json`` records the same four names as
    conditional for exactly this reason.
    """
    assert "cps" not in sys.modules, "Run these cases in a fresh interpreter"
    root = tmp_path_factory.mktemp("real-app")
    for name in ("config", "library", "ingest", "conversion", "tmp"):
        (root / name).mkdir()
    overrides = {
        "TMPDIR": str(root / "tmp"),
        "CALIBRE_DBPATH": str(root / "config"),
        "CWA_DB_PATH": str(root / "config"),
        "CWA_CALIBRE_LIBRARY_DIR": str(root / "library"),
        "CWA_INGEST_FOLDER": str(root / "ingest"),
        "CWA_TMP_CONVERSION_DIR": str(root / "conversion"),
        "CWA_DIRS_JSON": str(root / "dirs.json"),
    }
    with pytest.MonkeyPatch.context() as patch:
        for key, value in overrides.items():
            patch.setenv(key, value)
        patch.setattr(tempfile, "tempdir", str(root / "tmp"))
        patch.setattr(sys, "argv", ["cps", "-p", str(root / "config" / "app.db")])
        import cps
        from cps import services
        from cps.main import register_blueprints
        from cps.services.background_scheduler import BackgroundScheduler

        # Seed storage only, using the same empty-library schema as Docker.
        schema = Path(__file__).resolve().parents[3] / "scripts" / "metadata.db.sql"
        with sqlite3.connect(root / "library" / "metadata.db") as connection:
            connection.executescript(schema.read_text())
        cps.ub.init_db(str(root / "config" / "app.db"))
        key, error = cps.config_sql.get_encryption_key(str(root / "config"))
        assert not error
        cps.config_sql.load_configuration(cps.ub.session, key)
        settings = cps.ub.session.query(cps.config_sql._Settings).one()
        settings.config_calibre_dir = str(root / "library")
        if kobo_sync:
            settings.config_kobo_sync = True
            settings.config_kobo_two_way_annotation_sync = True
        cps.ub.session.commit()

        assert not cps.updater_thread.is_alive()
        assert cps.updater_thread.ident is None
        assert BackgroundScheduler._instance is None
        assert not cps._process_runtime_state.initialized
        print("LIFECYCLE before: updater_alive=False scheduler_exists=False initialized=False")
        try:
            app = cps.create_app(cps.config, services)
            register_blueprints(app)
            # create_app reloads the configuration object from storage, so this
            # asserts the *effective* switch the blueprint gate reads rather
            # than the row that was written above.
            assert bool(cps.config.config_kobo_sync) == kobo_sync
            # And assert the surface, not only the switch. A Kobo boot whose
            # reading-services blueprint failed to register answers 404 to every
            # annotation request no matter what the databases hold, which is
            # indistinguishable, from inside a case, from a product that refused
            # to answer. Fail here instead.
            assert ("readingservices_api_v3" in app.blueprints) == kobo_sync, sorted(
                app.blueprints)
            assert any(rule.rule == "/api/v3/content/<entitlement_id>/annotations"
                       for rule in app.url_map.iter_rules()) == kobo_sync
            app.config["CWA_TEST_ROOT"] = str(root)
            yield app
        finally:
            cps.updater_thread.stop()
            if cps.updater_thread.ident is not None:
                cps.updater_thread.join(timeout=5)
            scheduler = BackgroundScheduler._instance
            if scheduler is not None and scheduler.scheduler.running:
                scheduler.scheduler.shutdown(wait=True)
            assert not cps.updater_thread.is_alive()
            if scheduler is not None:
                assert not scheduler.scheduler.running
            print("LIFECYCLE teardown: updater_alive=False scheduler_running=False")


@pytest.fixture(scope="session")
def real_app(tmp_path_factory):
    yield from _boot_real_app(tmp_path_factory, kobo_sync=False)


@pytest.fixture(scope="session")
def kobo_real_app(tmp_path_factory):
    """The same real application, booted with the Kobo blueprints registered."""
    yield from _boot_real_app(tmp_path_factory, kobo_sync=True)
