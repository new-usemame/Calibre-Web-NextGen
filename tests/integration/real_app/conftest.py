"""Real application fixture. Run in a fresh interpreter; cps owns process state."""
import sqlite3
from pathlib import Path
import sys

import pytest


@pytest.fixture(scope="session")
def real_app(tmp_path_factory):
    assert "cps" not in sys.modules, "Run these cases in a fresh interpreter"
    root = tmp_path_factory.mktemp("real-app")
    for name in ("config", "library", "ingest", "conversion"):
        (root / name).mkdir()
    overrides = {
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
        cps.ub.session.commit()

        assert not cps.updater_thread.is_alive()
        assert cps.updater_thread.ident is None
        assert BackgroundScheduler._instance is None
        assert not cps._process_runtime_state.initialized
        print("LIFECYCLE before: updater_alive=False scheduler_exists=False initialized=False")
        try:
            app = cps.create_app(cps.config, services)
            register_blueprints(app)
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
