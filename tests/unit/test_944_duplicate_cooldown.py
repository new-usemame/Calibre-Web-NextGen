# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Duplicate auto-resolution cooldown: settings coercion + UTC time base (#944).

Two independent bugs made the auto-resolution cooldown unusable. Diagnosed by
@jdbway in #944 (mirrored upstream as CWA #1434/#1435).

1. Bool coercion. get_cwa_settings() collapses every int-valued setting to a
   bool unless the key is allowlisted in `integer_settings`. The allowlist was
   duplicated in two files that had to agree byte-for-byte:
   scripts/cwa_db.py (read by the scan task) and cps/cwa_functions.py (read by
   the settings page). They diverged by exactly one key — the cooldown — so the
   admin page round-tripped 30 correctly while the scan task saw bool(30) ->
   True -> int(True) -> a 1-minute cooldown. Any configured cooldown collapsed
   to 1 minute.

2. Mixed time bases. cwa_duplicate_resolutions.timestamp is written by the
   column's schema DEFAULT CURRENT_TIMESTAMP, which SQLite evaluates in UTC,
   but the cooldown compared it against a local datetime.now(). West of UTC
   that makes `elapsed` negative (-240 min in EDT), so `elapsed < cooldown` was
   permanently true and auto-resolution never ran at all. East of UTC the
   inverse: elapsed is inflated and the cooldown never fires.

The fix unifies on UTC rather than on local time. Existing rows are already UTC
(that is what the schema default wrote), so no data migration is required and
pre-existing history stays valid — see TestPreExistingRowsStayValid. Stamping
local instead would have required migrating every existing row, because
SELECT MAX(timestamp) over a mix of local and UTC strings returns the stale UTC
row, which keeps the cooldown broken on exactly the installs that already had
history.

The bugs are timezone-dependent, so these tests force a non-UTC zone. CI runs in
UTC, where local == UTC and neither bug can reproduce.
"""

import importlib
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def tz_new_york():
    """Force a west-of-UTC zone so the local/UTC skew is observable.

    In UTC (the CI default) local == UTC, elapsed is correct by accident, and
    the #944 timezone bug is invisible.
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path))
    from cwa_db import CWA_DB

    return CWA_DB(verbose=False)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
class TestCooldownSettingIsAnInteger:
    """Bug 1: a configured cooldown must reach the scan task as minutes."""

    @pytest.mark.parametrize("configured", [1, 5, 30, 60, 1440])
    def test_configured_cooldown_survives_as_int_not_bool(
        self, tmp_path, monkeypatch, configured
    ):
        db = _fresh_db(tmp_path, monkeypatch)
        db.cur.execute(
            "UPDATE cwa_settings SET duplicate_auto_resolve_cooldown_minutes=?;",
            (configured,),
        )
        db.con.commit()

        settings = db.get_cwa_settings()
        value = settings["duplicate_auto_resolve_cooldown_minutes"]

        assert not isinstance(value, bool), (
            f"cooldown coerced to bool ({value!r}); duplicate_scan does "
            f"int({value!r}) -> {int(value)}, so a {configured}-minute cooldown "
            f"silently becomes {int(value)} minute(s)"
        )
        assert value == configured

    def test_zero_cooldown_stays_zero(self, tmp_path, monkeypatch):
        """0 means disabled. It survived only because bool(0) -> int -> 0."""
        db = _fresh_db(tmp_path, monkeypatch)
        db.cur.execute(
            "UPDATE cwa_settings SET duplicate_auto_resolve_cooldown_minutes=0;"
        )
        db.con.commit()

        settings = db.get_cwa_settings()
        assert int(settings["duplicate_auto_resolve_cooldown_minutes"]) == 0

    def test_user_visible_symptom_thirty_minutes_is_not_one_minute(
        self, tmp_path, monkeypatch
    ):
        """The exact arithmetic duplicate_scan.py performs on the setting."""
        db = _fresh_db(tmp_path, monkeypatch)
        db.cur.execute(
            "UPDATE cwa_settings SET duplicate_auto_resolve_cooldown_minutes=30;"
        )
        db.con.commit()
        db.cwa_settings = db.get_cwa_settings()

        cooldown_minutes = int(
            db.cwa_settings.get("duplicate_auto_resolve_cooldown_minutes", 0)
        )
        assert cooldown_minutes == 30, (
            f"scan task computed a {cooldown_minutes}-minute cooldown from a "
            f"30-minute setting"
        )

    def test_cached_settings_refresh_after_update(self, tmp_path, monkeypatch):
        """update_cwa_settings must refresh the cache read by the scan task.

        duplicate_scan reads cwa_db.cwa_settings, populated once in __init__.
        Without a refresh a long-lived CWA_DB keeps serving pre-update values.
        """
        db = _fresh_db(tmp_path, monkeypatch)
        db.update_cwa_settings({"duplicate_auto_resolve_cooldown_minutes": 45})

        assert int(db.cwa_settings["duplicate_auto_resolve_cooldown_minutes"]) == 45


@pytest.mark.unit
class TestSettingsAllowlistHasOneSourceOfTruth:
    """Root cause of bug 1: the allowlist was duplicated across two files.

    The two copies had to stay byte-for-byte identical or a setting silently
    changed type depending on which file read it. They diverged by exactly the
    cooldown key. Aligning the copies would fix the symptom and leave the
    divergence class intact, so the constants now live in one module.
    """

    def test_cwa_functions_does_not_redefine_the_allowlists(self):
        source = _read(REPO_ROOT / "cps" / "cwa_functions.py")
        for name in ("integer_settings", "float_settings", "json_settings"):
            assert not re.search(rf"^\s*{name}\s*=\s*\[", source, flags=re.MULTILINE), (
                f"cps/cwa_functions.py redefines `{name}` as a literal list. "
                f"That is the duplication that caused #944 — import the shared "
                f"constant from cwa_db instead."
            )

    def test_both_consumers_resolve_to_the_same_object(self):
        import cwa_db

        assert cwa_db.INTEGER_SETTINGS is not None
        assert "duplicate_auto_resolve_cooldown_minutes" in cwa_db.INTEGER_SETTINGS

        functions_source = _read(REPO_ROOT / "cps" / "cwa_functions.py")
        assert "INTEGER_SETTINGS" in functions_source, (
            "cps/cwa_functions.py must consume the shared INTEGER_SETTINGS constant"
        )

    def test_every_integer_default_is_allowlisted(self, tmp_path, monkeypatch):
        """Catch the next divergence before a user does.

        Any schema default that is a non-bool int and is not allowlisted will be
        coerced to a bool the same way the cooldown was.
        """
        import cwa_db

        db = _fresh_db(tmp_path, monkeypatch)
        settings = db.get_cwa_settings()

        suspects = []
        for key, value in db.get_cwa_default_settings().items():
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if value in (0, 1):
                # Genuinely ambiguous: indistinguishable from a boolean flag.
                continue
            if key in cwa_db.INTEGER_SETTINGS or key in cwa_db.FLOAT_SETTINGS:
                continue
            suspects.append((key, value, settings.get(key)))

        assert not suspects, (
            f"int-valued settings missing from INTEGER_SETTINGS; each is "
            f"silently coerced to a bool, the #944 bug: {suspects}"
        )


@pytest.mark.unit
class TestResolutionTimestampsAreUTC:
    """Bug 2: the write side and the cooldown's read side must share a base."""

    def test_logged_resolution_is_not_in_the_future(
        self, tmp_path, monkeypatch, tz_new_york
    ):
        """The symptom, at its source: a row written now must not read as future.

        On main the schema default stamps UTC while the cooldown reads local, so
        a resolution logged 'now' appears ~4h in the future in EDT.
        """
        db = _fresh_db(tmp_path, monkeypatch)
        db.log_duplicate_resolution(
            group_hash="h1",
            group_title="T",
            group_author="A",
            kept_book_id=1,
            deleted_book_ids=[2],
            strategy="newest",
            trigger_type="automatic",
        )

        stored = db.cur.execute(
            "SELECT MAX(timestamp) FROM cwa_duplicate_resolutions "
            "WHERE trigger_type='automatic'"
        ).fetchone()[0]
        last_time = datetime.fromisoformat(stored).replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds() / 60

        assert elapsed >= 0, (
            f"resolution logged now reads as {abs(elapsed):.0f} min in the "
            f"future (stored={stored!r}) — the cooldown can never expire"
        )
        assert elapsed < 1

    def test_stamp_format_is_parseable_by_the_cooldown(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        db.log_duplicate_resolution(
            group_hash="h1",
            group_title="T",
            group_author="A",
            kept_book_id=1,
            deleted_book_ids=[2],
            strategy="newest",
            trigger_type="automatic",
        )
        stored = db.cur.execute(
            "SELECT timestamp FROM cwa_duplicate_resolutions"
        ).fetchone()[0]

        # datetime.fromisoformat is what the cooldown uses to parse it.
        assert datetime.fromisoformat(stored) is not None
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", stored), (
            f"unexpected timestamp shape {stored!r}"
        )


def _load_duplicate_scan_helper():
    """Import _cooldown_remaining_minutes without dragging in the cps app."""
    import cwa_db  # noqa: F401  (ensures scripts/ is importable first)

    task_path = REPO_ROOT / "cps" / "tasks" / "duplicate_scan.py"
    source = task_path.read_text(encoding="utf-8")
    namespace = {}
    # The helper is self-contained (datetime only); exec just its definition
    # rather than the module, which imports the whole cps package.
    match = re.search(
        r"^def _cooldown_remaining_minutes.*?(?=\n\S|\Z)",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, (
        "cps/tasks/duplicate_scan.py must expose a _cooldown_remaining_minutes "
        "helper so the cooldown decision is testable — it shipped broken "
        "precisely because it was buried in a 100-line method"
    )
    exec(
        "from datetime import datetime, timezone\n" + match.group(0),
        namespace,
    )
    return namespace["_cooldown_remaining_minutes"]


@pytest.mark.unit
class TestCooldownDecision:
    """The cooldown gate itself, driven against a real sqlite cursor."""

    def _db_with_resolution_at(self, tmp_path, monkeypatch, when_utc):
        db = _fresh_db(tmp_path, monkeypatch)
        db.cur.execute(
            "INSERT INTO cwa_duplicate_resolutions "
            "(timestamp, group_hash, group_title, group_author, kept_book_id, "
            " deleted_book_ids, strategy, trigger_type) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                when_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "h",
                "T",
                "A",
                1,
                "[2]",
                "newest",
                "automatic",
            ),
        )
        db.con.commit()
        return db

    def test_no_history_means_no_cooldown(self, tmp_path, monkeypatch, tz_new_york):
        remaining = _load_duplicate_scan_helper()
        db = _fresh_db(tmp_path, monkeypatch)
        assert remaining(db.cur, 30) == 0.0

    def test_recent_resolution_blocks(self, tmp_path, monkeypatch, tz_new_york):
        remaining = _load_duplicate_scan_helper()
        db = self._db_with_resolution_at(
            tmp_path, monkeypatch, datetime.now(timezone.utc) - timedelta(minutes=10)
        )
        left = remaining(db.cur, 30)
        assert 19 < left <= 20, f"expected ~20 min left, got {left}"

    def test_expired_cooldown_allows(self, tmp_path, monkeypatch, tz_new_york):
        remaining = _load_duplicate_scan_helper()
        db = self._db_with_resolution_at(
            tmp_path, monkeypatch, datetime.now(timezone.utc) - timedelta(minutes=31)
        )
        assert remaining(db.cur, 30) == 0.0

    def test_manual_resolutions_do_not_trigger_the_cooldown(
        self, tmp_path, monkeypatch, tz_new_york
    ):
        """The gate only considers trigger_type='automatic'."""
        remaining = _load_duplicate_scan_helper()
        db = _fresh_db(tmp_path, monkeypatch)
        db.cur.execute(
            "INSERT INTO cwa_duplicate_resolutions "
            "(timestamp, group_hash, group_title, group_author, kept_book_id, "
            " deleted_book_ids, strategy, trigger_type) VALUES (?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "h",
                "T",
                "A",
                1,
                "[2]",
                "newest",
                "manual",
            ),
        )
        db.con.commit()
        assert remaining(db.cur, 30) == 0.0


@pytest.mark.unit
class TestPreExistingRowsStayValid:
    """The fix must repair installs that already have history, not just fresh ones.

    Rows written before the fix carry the schema default's UTC stamp. Unifying on
    UTC keeps them valid with no migration. This test is the reason the fix
    stamps UTC rather than local: with a local stamp, SELECT MAX(timestamp) over
    a mix of local and legacy-UTC strings returns the stale UTC row, and the
    cooldown stays broken on exactly the installs that already had history.
    """

    def test_legacy_utc_row_is_read_correctly(
        self, tmp_path, monkeypatch, tz_new_york
    ):
        remaining = _load_duplicate_scan_helper()
        monkeypatch.setenv("CWA_DB_PATH", str(tmp_path))
        from cwa_db import CWA_DB

        db = CWA_DB(verbose=False)
        # Exactly what pre-fix code produced: column omitted, schema default fires.
        db.cur.execute(
            "INSERT INTO cwa_duplicate_resolutions "
            "(group_hash, group_title, group_author, kept_book_id, "
            " deleted_book_ids, strategy, trigger_type) VALUES (?,?,?,?,?,?,?)",
            ("h", "T", "A", 1, "[2]", "newest", "automatic"),
        )
        db.con.commit()

        left = remaining(db.cur, 30)
        assert 29 < left <= 30, (
            f"a legacy UTC row written just now should leave ~30 min of a "
            f"30-min cooldown, got {left}"
        )

    def test_legacy_utc_row_expires_on_schedule(
        self, tmp_path, monkeypatch, tz_new_york
    ):
        remaining = _load_duplicate_scan_helper()
        db = _fresh_db(tmp_path, monkeypatch)
        old = datetime.now(timezone.utc) - timedelta(minutes=45)
        db.cur.execute(
            "INSERT INTO cwa_duplicate_resolutions "
            "(timestamp, group_hash, group_title, group_author, kept_book_id, "
            " deleted_book_ids, strategy, trigger_type) VALUES (?,?,?,?,?,?,?,?)",
            (
                old.strftime("%Y-%m-%d %H:%M:%S"),
                "h",
                "T",
                "A",
                1,
                "[2]",
                "newest",
                "automatic",
            ),
        )
        db.con.commit()
        assert remaining(db.cur, 30) == 0.0


@pytest.mark.unit
class TestEndToEndCooldownIsUsable:
    """Both bugs at once: the reporter's configuration, start to finish."""

    def test_thirty_minute_cooldown_blocks_then_expires(
        self, tmp_path, monkeypatch, tz_new_york
    ):
        remaining = _load_duplicate_scan_helper()
        db = _fresh_db(tmp_path, monkeypatch)
        db.update_cwa_settings({"duplicate_auto_resolve_cooldown_minutes": 30})

        cooldown_minutes = int(
            db.cwa_settings.get("duplicate_auto_resolve_cooldown_minutes", 0)
        )
        assert cooldown_minutes == 30

        db.log_duplicate_resolution(
            group_hash="h",
            group_title="T",
            group_author="A",
            kept_book_id=1,
            deleted_book_ids=[2],
            strategy="newest",
            trigger_type="automatic",
        )
        # Immediately after a resolution the gate holds for ~the full window.
        left = remaining(db.cur, cooldown_minutes)
        assert 29 < left <= 30, f"expected ~30 min of cooldown, got {left}"

        # And it lets the next scan through once the window has passed.
        db.cur.execute(
            "UPDATE cwa_duplicate_resolutions SET timestamp=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=31)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            ),
        )
        db.con.commit()
        assert remaining(db.cur, cooldown_minutes) == 0.0
