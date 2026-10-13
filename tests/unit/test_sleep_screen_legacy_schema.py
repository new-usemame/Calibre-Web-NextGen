"""The additive sleep-screen migration makes full current Device reads safe."""

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


def test_sleep_screen_migration_upgrades_legacy_devices_without_rewriting_them():
    from cps import ub

    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE device_sleep_screen"))
        conn.execute(text("ALTER TABLE device DROP COLUMN sleep_screen_capability"))
        conn.execute(text(
            "INSERT INTO device (id, public_id, user_id, kind, display_name, "
            "first_seen_at, last_seen_at, active, created_by, share_place) "
            "VALUES (1, 'legacy-reader', 1, 'koreader', 'A reader', "
            "'2026-01-01', '2026-01-01', 1, 'auto', 1)"
        ))
    session = sessionmaker(bind=engine)()
    try:
        ub.migrate_device_sleep_screen(engine, session)
        ub.migrate_device_sleep_screen(engine, session)
        device = session.query(ub.Device).one()
        assert device.display_name == "A reader"
        assert device.share_place is True
        assert device.sleep_screen_capability is None
        assert session.query(ub.DeviceSleepScreen).count() == 0
        assert inspect(engine).has_table("device_sleep_screen")
    finally:
        session.close()
        engine.dispose()
