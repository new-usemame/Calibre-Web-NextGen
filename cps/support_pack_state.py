# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""State collectors for the support pack (fork issue #2728).

The support pack used to carry settings and logs only. These collectors add
the state a maintainer otherwise has to ask for: runtime and container limits,
storage by role, database health, and background work.

Design rules, so the pack stays safe to paste into a public issue:

* **Allow-list, never a redaction pass.** Each collector names the fields it
  emits. Nothing is copied wholesale and then scrubbed.
* **Counts, versions and sizes only.** No paths, no IPs, no usernames, no
  titles, no row contents. Storage is reported per role (config, library,
  ingest) with free space and mount type, never the literal path, because
  paths often contain a username or a NAS share name.
* **Failures are isolated and quiet.** A collector that raises produces
  ``{"error": "<ExceptionType>"}`` for its own section only. The exception
  *message* is never included: it can quote a path.
* **Bounded.** Each section is capped at ``MAX_SECTION_BYTES`` and database
  work has a time budget, so a 300,000-book library cannot blow up the pack
  or stall the request.

Every function takes its inputs as arguments so it can be tested without
booting the app; ``cps.debug_info`` resolves the real paths.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_SECTION_BYTES = 64 * 1024
DB_TIME_BUDGET_SECONDS = 10.0
INGEST_COUNT_CAP = 10_000
MAX_JOBS_LISTED = 100
MAX_FORMATS_LISTED = 50

# Environment variables whose *values* are safe to show (they are settings,
# not secrets or paths). Everything else matching ``_ENV_NAME_PATTERN`` is
# reported by name only, so HARDCOVER_TOKEN and any path-valued CWA_* variable
# never leak a value.
_SAFE_ENV_VALUES = (
    "TZ",
    "PUID",
    "PGID",
    "NETWORK_SHARE_MODE",
    "CWA_PORT_OVERRIDE",
    "CWA_CALIBRE_USER_PLUGINS",
)
_ENV_NAME_PATTERN = re.compile(r"^(CWA_|CALIBRE_|HARDCOVER_|FLASK_)")
# A safe value is short and path-free, so a surprising value degrades to "set".
_SAFE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_+.:-]+(?:/[A-Za-z0-9_+.-]+){0,2}$")

# Filesystem types where SQLite locking and latency problems are common.
_NETWORK_FS_TYPES = frozenset(
    {"nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "glusterfs", "ceph", "afs",
     "fuse.sshfs", "fuse.rclone"}
)


def _error(ex: BaseException) -> dict:
    """Error marker carrying the exception type only, never its message."""
    return {"error": type(ex).__name__}


def _read_text(path: str):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fd:
            return fd.read().strip()
    except OSError:
        return None


def _int_or_none(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- runtime

def _cgroup_limits() -> dict:
    """Memory and CPU limits from cgroup v2, falling back to v1."""
    out: dict = {}
    v2 = "/sys/fs/cgroup"
    mem_max = _read_text(f"{v2}/memory.max")
    if mem_max is not None:
        out["version"] = 2
        out["memory_limit_bytes"] = None if mem_max == "max" else _int_or_none(mem_max)
        out["memory_current_bytes"] = _int_or_none(_read_text(f"{v2}/memory.current"))
        cpu_max = _read_text(f"{v2}/cpu.max")
        if cpu_max:
            quota, _, period = cpu_max.partition(" ")
            q, p = _int_or_none(quota), _int_or_none(period)
            out["cpu_limit_cores"] = round(q / p, 2) if q and p else None
        return out
    v1_mem = _read_text("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if v1_mem is not None:
        out["version"] = 1
        limit = _int_or_none(v1_mem)
        # v1 reports "unlimited" as a huge number rather than a keyword.
        out["memory_limit_bytes"] = None if limit is None or limit >= 1 << 60 else limit
        quota = _int_or_none(_read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"))
        period = _int_or_none(_read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
        out["cpu_limit_cores"] = round(quota / period, 2) if quota and quota > 0 and period else None
    return out


def _mem_total_bytes():
    meminfo = _read_text("/proc/meminfo")
    if not meminfo:
        return None
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def _safe_env_values(env) -> dict:
    """Values of the allow-listed settings that are set in ``env``.

    Every key is written out literally, not looped over, so the environment
    SSOT test can resolve each read statically and check it against
    ``examples/.env.example``. Keep this in step with ``_SAFE_ENV_VALUES``
    (a test asserts they match).
    """
    raw = {
        "TZ": env.get("TZ"),
        "PUID": env.get("PUID"),
        "PGID": env.get("PGID"),
        "NETWORK_SHARE_MODE": env.get("NETWORK_SHARE_MODE"),
        "CWA_PORT_OVERRIDE": env.get("CWA_PORT_OVERRIDE"),
        "CWA_CALIBRE_USER_PLUGINS": env.get("CWA_CALIBRE_USER_PLUGINS"),
    }
    values = {}
    for name, value in raw.items():
        if value is None:
            continue
        text = str(value)
        values[name] = text if len(text) <= 64 and _SAFE_VALUE_PATTERN.match(text) else "set"
    return values


def collect_runtime(environ=None) -> dict:
    """Build and runtime facts. Versions of Calibre, Kepubify and the Python
    packages are already in ``libs.txt`` and are not repeated here."""
    from . import constants

    env = os.environ if environ is None else environ
    values = _safe_env_values(env)
    names_only = sorted(
        n for n in env if _ENV_NAME_PATTERN.match(n) and n not in _SAFE_ENV_VALUES
    )
    process = {}
    if hasattr(os, "getuid"):
        process = {"uid": os.getuid(), "gid": os.getgid()}
    return {
        "cwng_version": constants.INSTALLED_VERSION,
        "version_stamped": bool(getattr(constants, "VERSION_IS_STAMPED", False)),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "system": platform.system(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "containerised": os.path.exists("/.dockerenv"),
        "cpu_count": os.cpu_count(),
        "mem_total_bytes": _mem_total_bytes(),
        "cgroup": _cgroup_limits(),
        "process": process,
        "env_values": values,
        "env_names_set": names_only,
    }


# ---------------------------------------------------------------- storage

def _unescape_mount_field(field: str) -> str:
    """mountinfo octal-escapes space, tab, newline and backslash."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def read_mounts(mountinfo_text=None) -> list:
    """Parse /proc/self/mountinfo into ``(mount_point, fs_type, options)``."""
    text = mountinfo_text if mountinfo_text is not None else _read_text("/proc/self/mountinfo")
    mounts = []
    for line in (text or "").splitlines():
        left, sep, right = line.partition(" - ")
        fields = left.split()
        right_fields = right.split()
        if not sep or len(fields) < 6 or not right_fields:
            continue
        mounts.append((_unescape_mount_field(fields[4]), right_fields[0], fields[5]))
    return mounts


def mount_for(path: str, mounts: list):
    """The mount entry with the longest mount point containing ``path``."""
    real = os.path.realpath(path)
    best = None
    for mount_point, fs_type, options in mounts:
        prefix = mount_point.rstrip("/")
        if real == mount_point or real.startswith(prefix + "/") or mount_point == "/":
            if best is None or len(mount_point) > len(best[0]):
                best = (mount_point, fs_type, options)
    return best


def collect_storage(roles: dict, mounts=None) -> dict:
    """Free space, writability and mount type per role. Never a path."""
    mounts = read_mounts() if mounts is None else mounts
    out = {}
    for role, path in roles.items():
        if not path:
            out[role] = {"configured": False}
            continue
        entry = {"configured": True, "exists": os.path.isdir(path)}
        if entry["exists"]:
            try:
                usage = shutil.disk_usage(path)
                entry["free_bytes"] = usage.free
                entry["total_bytes"] = usage.total
            except OSError as ex:
                entry["usage"] = _error(ex)
            entry["writable"] = os.access(path, os.W_OK)
            mount = mount_for(path, mounts)
            if mount:
                entry["fs_type"] = mount[1]
                entry["network_fs"] = mount[1] in _NETWORK_FS_TYPES
                entry["mount_read_only"] = "ro" in mount[2].split(",")
        out[role] = entry
    return out


# -------------------------------------------------------------- databases

def _open_readonly(path: str) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    return conn


def collect_database(path, budget_s: float = DB_TIME_BUDGET_SECONDS, formats: bool = False) -> dict:
    """Health and size facts for one SQLite database, opened read-only.

    Row *counts* per table only, never row contents. ``formats`` adds the
    per-format book count for a Calibre ``metadata.db``.
    """
    if not path or not os.path.isfile(path):
        return {"exists": False}
    entry: dict = {"exists": True, "size_bytes": os.path.getsize(path)}
    wal = path + "-wal"
    if os.path.isfile(wal):
        entry["wal_bytes"] = os.path.getsize(wal)
    try:
        conn = _open_readonly(path)
    except Exception as ex:  # pylint: disable=broad-except
        entry.update(_error(ex))
        return entry
    try:
        deadline = time.monotonic() + budget_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        entry["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        entry["user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
        entry["page_size"] = conn.execute("PRAGMA page_size").fetchone()[0]
        entry["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
        # quick_check, not integrity_check: it skips the index/content
        # cross-checks that make integrity_check slow on a large library.
        result = conn.execute("PRAGMA quick_check(1)").fetchone()
        entry["quick_check"] = str(result[0])[:200] if result else "no result"
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            except sqlite3.OperationalError as ex:
                if "interrupt" in str(ex).lower():
                    raise
                counts[table] = None
            except sqlite3.Error:
                counts[table] = None
        entry["row_counts"] = counts
        if formats and "data" in counts:
            rows = conn.execute(
                "SELECT format, COUNT(*) FROM data GROUP BY format ORDER BY 2 DESC LIMIT ?",
                (MAX_FORMATS_LISTED,),
            ).fetchall()
            entry["formats"] = {str(fmt).upper(): n for fmt, n in rows}
    except sqlite3.OperationalError as ex:
        if "interrupt" in str(ex).lower():
            entry["timed_out"] = True
        else:
            entry.update(_error(ex))
    except Exception as ex:  # pylint: disable=broad-except
        entry.update(_error(ex))
    finally:
        conn.close()
    return entry


def collect_databases(paths: dict) -> dict:
    out = {}
    for name, path in paths.items():
        out[name] = collect_database(path, formats=(name == "metadata_db"))
    return out


# -------------------------------------------------------------- scheduler

def _scheduler_instance():
    from .services.background_scheduler import BackgroundScheduler
    return BackgroundScheduler._instance  # pylint: disable=protected-access


def _count_ingest_entries(path, cap: int) -> dict:
    if not path or not os.path.isdir(path):
        return {"exists": False}
    count = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.name.startswith("."):
                continue
            count += 1
            if count >= cap:
                return {"exists": True, "entries": count, "capped": True}
    return {"exists": True, "entries": count, "capped": False}


def collect_background(cwa_db_path, ingest_path, scheduler=None, ingest_cap: int = INGEST_COUNT_CAP) -> dict:
    """Scheduled jobs and ingest queue depth. Job ids and names are left out
    on purpose: they can embed book and user ids."""
    out: dict = {}
    try:
        instance = scheduler if scheduler is not None else _scheduler_instance()
        jobs = list(instance.get_jobs()) if instance is not None else []
        out["scheduler"] = {
            "available": instance is not None,
            "job_count": len(jobs),
            "jobs": [
                {
                    "func": str(getattr(job, "func_ref", None) or "unknown"),
                    "trigger": type(getattr(job, "trigger", None)).__name__,
                    "paused": getattr(job, "next_run_time", None) is None,
                }
                for job in jobs[:MAX_JOBS_LISTED]
            ],
        }
    except Exception as ex:  # pylint: disable=broad-except
        out["scheduler"] = _error(ex)

    out["scheduled_send_jobs"] = _scheduled_send_state(cwa_db_path)
    try:
        out["ingest_queue"] = _count_ingest_entries(ingest_path, ingest_cap)
    except Exception as ex:  # pylint: disable=broad-except
        out["ingest_queue"] = _error(ex)
    return out


def _scheduled_send_state(cwa_db_path) -> dict:
    """Counts of ``cwa_scheduled_jobs`` by state, no users, titles or ids."""
    if not cwa_db_path or not os.path.isfile(cwa_db_path):
        return {"exists": False}
    try:
        conn = _open_readonly(cwa_db_path)
    except Exception as ex:  # pylint: disable=broad-except
        return _error(ex)
    try:
        rows = conn.execute(
            "SELECT state, COUNT(*), SUM(CASE WHEN COALESCE(last_error, '') != '' THEN 1 ELSE 0 END) "
            "FROM cwa_scheduled_jobs GROUP BY state"
        ).fetchall()
        return {
            "exists": True,
            "by_state": {str(s): {"count": n, "with_error": int(e or 0)} for s, n, e in rows},
        }
    except Exception as ex:  # pylint: disable=broad-except
        return _error(ex)
    finally:
        conn.close()


# ---------------------------------------------------------- orchestration

def _encode(section: dict) -> tuple:
    """Serialise a section, replacing an oversize one with a truncation
    marker. Returns ``(bytes, truncated)`` so the manifest can say so."""
    data = json.dumps(section, sort_keys=True, indent=2).encode("utf-8")
    if len(data) > MAX_SECTION_BYTES:
        marker = {"truncated": True, "original_bytes": len(data)}
        return json.dumps(marker, sort_keys=True, indent=2).encode("utf-8"), True
    return data, False


def collect_all(paths: dict, sanitized: bool, mounts=None, scheduler=None, environ=None) -> dict:
    """Return ``{archive filename: bytes}`` for the manifest and every section.

    ``paths`` keys: ``config``, ``library``, ``ingest`` (directories),
    ``library_books`` (directory, split libraries only) and ``app_db``,
    ``cwa_db``, ``metadata_db`` (files). Any may be ``None``.
    """
    collectors = {
        "state/runtime.json": lambda: collect_runtime(environ),
        "state/storage.json": lambda: collect_storage(
            {
                role: paths.get(role)
                for role in ("config", "library", "library_books", "ingest")
                # library_books exists only for a split library.
                if role != "library_books" or paths.get("library_books")
            },
            mounts,
        ),
        "state/databases.json": lambda: collect_databases(
            {name: paths.get(name) for name in ("app_db", "cwa_db", "metadata_db")}
        ),
        "state/background.json": lambda: collect_background(
            paths.get("cwa_db"), paths.get("ingest"), scheduler
        ),
    }
    files: dict = {}
    manifest_sections = []
    for filename, collect in collectors.items():
        try:
            section = collect()
        except Exception as ex:  # pylint: disable=broad-except
            section = _error(ex)
        data, truncated = _encode(section)
        files[filename] = data
        manifest_sections.append({
            "file": filename,
            "ok": "error" not in section and not truncated,
            "bytes": len(data),
        })

    from . import constants
    manifest = {
        "kind": "cwng-support-pack",
        "schema_version": SCHEMA_VERSION,
        "cwng_version": constants.INSTALLED_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sanitized": bool(sanitized),
        "sections": manifest_sections,
    }
    files["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    return files
