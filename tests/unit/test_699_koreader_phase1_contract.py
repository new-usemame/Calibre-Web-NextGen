from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services.annotation_portable import apply_portable

pytestmark = pytest.mark.unit


def test_identity_migration_is_idempotent_and_preserves_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE annotation (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL, annotation_id VARCHAR NOT NULL
            )
        """))
        conn.execute(text(
            "INSERT INTO annotation (user_id, book_id, annotation_id) VALUES (1, 2, 'a')"
        ))
    ub.migrate_annotation_koreader_identity(engine, None)
    ub.migrate_annotation_koreader_identity(engine, None)
    columns = {c["name"] for c in inspect(engine).get_columns("annotation")}
    assert {"start_xpointer", "end_xpointer"} <= columns
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM annotation")).scalar_one() == 1
        indexes = {r[1] for r in conn.execute(text("PRAGMA index_list(annotation)"))}
    assert "uq_annotation_user_book_annotation" in indexes


def test_identity_migration_rolls_back_on_preexisting_duplicates(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dups.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE annotation (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL, annotation_id VARCHAR NOT NULL
            )
        """))
        conn.execute(text(
            "INSERT INTO annotation (user_id, book_id, annotation_id) VALUES "
            "(1, 2, 'a'), (1, 2, 'a')"
        ))
    with pytest.raises(RuntimeError, match="duplicate"):
        ub.migrate_annotation_koreader_identity(engine, None)
    assert "start_xpointer" not in {c["name"] for c in inspect(engine).get_columns("annotation")}


def test_two_parallel_devices_merge_without_duplicate_or_integrity_leak(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}", future=True,
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    barrier = Barrier(2)
    book = SimpleNamespace(id=7, uuid="book")

    def device(text_value):
        session = Session()
        first = True

        def commit():
            nonlocal first
            if first:
                first = False
                barrier.wait(timeout=5)
            session.commit()

        try:
            return apply_portable(
                {"annotation_id": "shared", "highlighted_text": text_value},
                user_id=3, book=book, session=session, commit=commit,
            )[1]
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        actions = list(pool.map(device, ("device-a", "device-b")))
    with Session() as session:
        rows = session.query(ub.Annotation).filter_by(
            user_id=3, book_id=7, annotation_id="shared"
        ).all()
        assert len(rows) == 1
        assert rows[0].highlighted_text in {"device-a", "device-b"}
    assert "created" in actions


def test_two_parallel_devices_with_distinct_highlights_never_clobber(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'distinct.db'}", future=True,
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    barrier = Barrier(2)
    book = SimpleNamespace(id=7, uuid="book")

    def device(annotation_id):
        session = Session()
        try:
            barrier.wait(timeout=5)
            return apply_portable(
                {"annotation_id": annotation_id, "highlighted_text": annotation_id},
                user_id=3, book=book, session=session, commit=session.commit,
            )[1]
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        actions = list(pool.map(device, ("device-a-highlight", "device-b-highlight")))
    with Session() as session:
        rows = session.query(ub.Annotation).filter_by(user_id=3, book_id=7).all()
        assert {row.annotation_id for row in rows} == {
            "device-a-highlight", "device-b-highlight",
        }
    assert actions == ["created", "created"]


def test_plugin_native_provider_and_handshake_are_wired():
    root = Path(__file__).parents[2]
    provider = (root / "koreader/plugins/cwasync.koplugin/koreader_annotations_provider.lua").read_text()
    main = (root / "koreader/plugins/cwasync.koplugin/main.lua").read_text()
    client = (root / "koreader/plugins/cwasync.koplugin/CWASyncClient.lua").read_text()
    assert 'ui.annotation.annotations' in provider
    assert 'position_type = rolling and "koreader_xpointer"' in provider
    assert "push_all_local = true" in provider
    assert "client:pull_annotations" in main and "client:push_annotations" in main
    assert "function CWASyncClient:authorize" in client
