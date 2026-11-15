# SPDX-License-Identifier: GPL-3.0-or-later
"""Separate Kobo removal/restore work from retained reading observations."""
from sqlalchemy import inspect, text

revision = "kobo_device_removal_b"
down_revision = "device_book_scope_a"
branch_labels = None
depends_on = None


def _bind(connection):
    if connection is not None:
        return connection
    from alembic import op
    return op.get_bind()


def upgrade(connection=None):
    connection = _bind(connection)
    from cps.ub import KoboDeviceBookRemoval, KoboDeviceDeletionCapture
    KoboDeviceBookRemoval.__table__.create(connection, checkfirst=True)
    KoboDeviceDeletionCapture.__table__.create(connection, checkfirst=True)
    columns = {c["name"] for c in inspect(connection).get_columns("device_reading_position")}
    if "rehydrate_armed_at" not in columns:
        connection.execute(text("ALTER TABLE device_reading_position ADD COLUMN rehydrate_armed_at DATETIME"))
        # Preserve the fence of already armed work without changing observations.
        connection.execute(text("UPDATE device_reading_position SET rehydrate_armed_at = server_modified_at WHERE rehydrate_needed = 1"))
    # Existing device observations are activity too. There is no trustworthy
    # pre-upgrade PATCH receipt, so those departures get the bounded hold.
    observed = [name for name in ('progress_percent', 'location_value', 'reading_status')
                if name in columns]
    if observed:
        receipt = 'server_modified_at'
        if 'status_received_at' in columns:
            receipt = ('CASE WHEN status_received_at > server_modified_at '
                       'THEN status_received_at ELSE server_modified_at END')
        predicate = ' OR '.join(name + ' IS NOT NULL' for name in observed)
        connection.execute(text(
            'INSERT INTO kobo_device_book_removal (device_id, book_id, activity_received_at) '
            'SELECT device_id, book_id, ' + receipt + ' FROM device_reading_position '
            'WHERE ' + predicate + ' ON CONFLICT (device_id, book_id) DO NOTHING'))


def downgrade(connection=None):
    connection = _bind(connection)
    from cps.ub import KoboDeviceBookRemoval, KoboDeviceDeletionCapture
    KoboDeviceDeletionCapture.__table__.drop(connection, checkfirst=True)
    KoboDeviceBookRemoval.__table__.drop(connection, checkfirst=True)
    columns = {c["name"] for c in inspect(connection).get_columns("device_reading_position")}
    if "rehydrate_armed_at" in columns:
        connection.execute(text("ALTER TABLE device_reading_position DROP COLUMN rehydrate_armed_at"))
