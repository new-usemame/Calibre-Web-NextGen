# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for CWA_DB.execute_write / execute_read (#876).

The Hardcover auto-fetch feature calls two CWA_DB convenience helpers that the
class never defined, so every call raised AttributeError:

* ``cps/tasks/auto_hardcover_id.py`` ``_save_stats`` -> ``execute_write`` — the
  failure the reporter saw, swallowed into a WARN, leaving the stats table empty.
* ``cps/cwa_functions.py`` (Stats & Activity tab) -> ``execute_read`` — the same
  defect's silent half, blanking the admin Hardcover panel.

``execute_read`` must return ``fetchall()`` rows: the admin panel indexes
``total_processed[0][0]``, so a ``fetchone()`` contract would break it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cwa_db import CWA_DB

pytestmark = pytest.mark.unit


class TestExecuteHelpersExist:
    """The helpers are a published contract — three callsites depend on them."""

    def test_cwa_db_defines_both_execute_helpers(self):
        """Source-pin: a refactor must not silently drop these and re-break #876."""
        assert callable(getattr(CWA_DB, "execute_write", None)), (
            "CWA_DB.execute_write is missing — auto_hardcover_id._save_stats calls it"
        )
        assert callable(getattr(CWA_DB, "execute_read", None)), (
            "CWA_DB.execute_read is missing — cwa_functions Stats tab calls it"
        )


class TestExecuteWrite:
    def test_write_is_committed_and_survives_a_fresh_connection(self, temp_cwa_db, tmp_path):
        """Pins the commit, not just the execute.

        A helper that ran cur.execute() without con.commit() would still satisfy
        a same-connection read, but the row would be lost to every other reader
        (the admin panel opens its own CWA_DB). Assert durability across a
        brand-new sqlite connection to the file on disk.
        """
        temp_cwa_db.execute_write(
            "INSERT INTO hardcover_auto_fetch_stats "
            "(timestamp, books_processed, auto_matched, queued_for_review, "
            "skipped_no_results, errors, avg_confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2026-07-14T10:00:00", 364, 12, 332, 20, 0, 0.91),
        )

        fresh = sqlite3.connect(str(Path(tmp_path) / "cwa.db"))
        try:
            rows = fresh.execute(
                "SELECT books_processed, auto_matched, avg_confidence "
                "FROM hardcover_auto_fetch_stats"
            ).fetchall()
        finally:
            fresh.close()

        assert rows == [(364, 12, 0.91)]

    def test_params_are_bound_not_interpolated(self, temp_cwa_db):
        """A value containing SQL must land as data, never as executed SQL."""
        hostile = "'); DROP TABLE hardcover_auto_fetch_stats; --"
        temp_cwa_db.execute_write(
            "INSERT INTO hardcover_auto_fetch_stats "
            "(timestamp, books_processed, auto_matched, queued_for_review, "
            "skipped_no_results, errors, avg_confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (hostile, 1, 0, 0, 0, 0, 0.0),
        )

        rows = temp_cwa_db.execute_read(
            "SELECT timestamp FROM hardcover_auto_fetch_stats"
        )
        assert rows == [(hostile,)], "table still stands and the value was stored verbatim"

    def test_params_default_to_empty_so_paramless_statements_work(self, temp_cwa_db):
        temp_cwa_db.execute_write(
            "INSERT INTO hardcover_auto_fetch_stats "
            "(timestamp, books_processed, auto_matched, queued_for_review, "
            "skipped_no_results, errors, avg_confidence) "
            "VALUES ('2026-07-14T10:00:00', 5, 1, 4, 0, 0, 0.5)"
        )
        assert temp_cwa_db.execute_read(
            "SELECT books_processed FROM hardcover_auto_fetch_stats"
        ) == [(5,)]


class TestExecuteRead:
    def test_returns_list_of_rows_indexable_as_row_then_column(self, temp_cwa_db):
        """Pins the [0][0] contract cwa_functions.py depends on (fetchall, not fetchone)."""
        temp_cwa_db.execute_write(
            "INSERT INTO hardcover_auto_fetch_stats "
            "(timestamp, books_processed, auto_matched, queued_for_review, "
            "skipped_no_results, errors, avg_confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2026-07-14T10:00:00", 364, 12, 332, 20, 0, 0.91),
        )

        result = temp_cwa_db.execute_read(
            "SELECT SUM(books_processed) FROM hardcover_auto_fetch_stats"
        )

        assert isinstance(result, list), "fetchone() would break total_processed[0][0]"
        assert result[0][0] == 364

    def test_returns_every_row_not_just_the_first(self, temp_cwa_db):
        for processed in (10, 20, 30):
            temp_cwa_db.execute_write(
                "INSERT INTO hardcover_auto_fetch_stats "
                "(timestamp, books_processed, auto_matched, queued_for_review, "
                "skipped_no_results, errors, avg_confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("2026-07-14T10:00:00", processed, 0, 0, 0, 0, 0.0),
            )

        rows = temp_cwa_db.execute_read(
            "SELECT books_processed FROM hardcover_auto_fetch_stats ORDER BY books_processed"
        )
        assert rows == [(10,), (20,), (30,)]

    def test_empty_aggregate_keeps_the_admin_panel_guard_correct(self, temp_cwa_db):
        """A never-run library: SUM over zero rows yields [(None,)], not [].

        cwa_functions.py guards with ``total_processed[0][0] if total_processed
        and total_processed[0][0] else 0``. Pin that this shape holds so the
        panel renders 0 rather than raising IndexError.
        """
        result = temp_cwa_db.execute_read(
            "SELECT SUM(books_processed) FROM hardcover_auto_fetch_stats"
        )

        assert result == [(None,)]
        rendered = result[0][0] if result and result[0][0] else 0
        assert rendered == 0

    def test_empty_row_set_returns_empty_list(self, temp_cwa_db):
        assert temp_cwa_db.execute_read(
            "SELECT books_processed FROM hardcover_auto_fetch_stats"
        ) == []


class TestHardcoverStatsEndToEnd:
    """The two callsites meet here: the task writes, the admin panel reads."""

    def test_save_stats_lands_a_row_the_admin_panel_can_total(self, temp_cwa_db):
        """Reproduces the reporter's exact symptom red->green.

        Before the fix _save_stats swallowed the AttributeError into a WARN and
        the table stayed empty forever, so this asserts on rows, not on a raise.
        """
        from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID

        task = TaskAutoHardcoverID()
        task.books_processed = 364
        task.auto_matched = 12
        task.queued_for_review = 332
        task.skipped_no_results = 20
        task.errors = 0
        task.total_confidence = 12 * 0.91

        task._save_stats()

        rows = temp_cwa_db.execute_read(
            "SELECT books_processed, auto_matched, queued_for_review, "
            "skipped_no_results, errors FROM hardcover_auto_fetch_stats"
        )
        assert rows == [(364, 12, 332, 20, 0)], (
            "auto-fetch stats never reached the database (#876)"
        )

        # The admin Stats & Activity tab's exact query + guard.
        total_processed = temp_cwa_db.execute_read(
            "SELECT SUM(books_processed) FROM hardcover_auto_fetch_stats"
        )
        assert (total_processed[0][0] if total_processed and total_processed[0][0] else 0) == 364

    def test_save_stats_records_average_confidence(self, temp_cwa_db):
        from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID

        task = TaskAutoHardcoverID()
        task.books_processed = 2
        task.auto_matched = 2
        task.total_confidence = 1.8

        task._save_stats()

        rows = temp_cwa_db.execute_read(
            "SELECT avg_confidence FROM hardcover_auto_fetch_stats"
        )
        assert rows[0][0] == pytest.approx(0.9)
