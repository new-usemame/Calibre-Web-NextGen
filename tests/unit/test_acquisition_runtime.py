# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit startup lifetime must not manufacture a new database or lost key."""
import importlib
import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest
from sqlalchemy import MetaData, create_engine

package_path = Path(__file__).resolve().parents[2] / "cps/services/acquisition"
spec = importlib.util.spec_from_file_location("_acquisition_runtime_tests",
    package_path / "__init__.py", submodule_search_locations=[str(package_path)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
r = importlib.import_module(spec.name + ".runtime")


def create_schema(path):
    engine = create_engine("sqlite:///" + str(path))
    metadata = MetaData()
    r.define_tables(metadata)
    metadata.create_all(engine)
    engine.dispose()


def test_missing_or_old_database_is_not_created_or_migrated(tmp_path):
    path = tmp_path / "app.db"
    with pytest.raises(r.StorageError):
        with r.open_ingest_repository(path):
            pytest.fail("Missing database accepted")
    assert not path.exists() and not (tmp_path / "acquisition.key").exists()
    with sqlite3.connect(path) as c:
        c.execute("create table preserved (id integer primary key, value text)")
        c.execute("insert into preserved values (1, 'existing')")
    before = path.read_bytes()
    with pytest.raises(r.StorageError):
        with r.open_repository(path, initialize_key=True):
            pytest.fail("Old database accepted without migration")
    assert path.read_bytes() == before
    assert not (tmp_path / "acquisition.key").exists()


def test_explicit_setup_restart_and_lost_key_preserve_existing_credentials(tmp_path):
    path = tmp_path / "app db with spaces.db"
    create_schema(path)
    with pytest.raises(ValueError):
        with r.open_ingest_repository(path):
            pytest.fail("Ingest created a missing key")
    with r.open_repository(path, initialize_key=True) as repo:
        connection = repo.create_connection("Catalog", "opds", {"credential": "PRIVATE"}, enabled=True)
        offer = repo.create_offer(1, connection.id, {"href": "https://example.org/book.epub"})
        job = repo.create_job(1, offer, "intent", requires_approval=False)
    with r.open_ingest_repository(path) as repo:
        claim = repo.claim()
        assert claim.job.id == job.id
        assert repo.material(job.id, claim.token).config["credential"] == "PRIVATE"
        with repo.engine.connect() as conn:
            assert conn.exec_driver_sql("pragma foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("pragma journal_mode").scalar() == "delete"
    (tmp_path / "acquisition.key").unlink()
    before = path.read_bytes()
    with pytest.raises(ValueError):
        with r.open_repository(path, initialize_key=True):
            pytest.fail("Encrypted database got a replacement key")
    assert not (tmp_path / "acquisition.key").exists()
    assert path.read_bytes() == before
