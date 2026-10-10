# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Discover formats accepted by the configured Calibre installation.

The editor and conversion endpoints must agree with the converter that will
actually run. Calibre's initialized plugin registry is the source of truth;
``ebook-convert --help`` does not provide a format inventory. This module asks
the matching ``calibre-debug`` executable to report Calibre's input and output
plugin registries using the same environment as a conversion task.

An unavailable or malformed probe fails closed. In particular, user-installed
format plugins are never assumed to be present just because they are commonly
used. Kepubify is handled separately by ``helper.get_convert_options``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import calibre_user_plugins
from . import parallel

try:
    import gevent
    from gevent.event import AsyncResult as _GeventAsyncResult
    from gevent.lock import Semaphore as _GeventSemaphore

    _HAVE_GEVENT = True
except ImportError:  # pragma: no cover - fallback for minimal installations
    gevent = None
    _GeventAsyncResult = None
    _GeventSemaphore = None
    _HAVE_GEVENT = False


_PROBE_MARKER = "CWNG_CONVERSION_CAPABILITIES="
_PROBE_CODE = (
    "import json; "
    "from calibre.customize.ui import available_input_formats, available_output_formats; "
    f"print({_PROBE_MARKER!r} + json.dumps({{'inputs': sorted(available_input_formats()), "
    "'outputs': sorted(available_output_formats())}))"
)
_CACHE_TTL_SECONDS = 60.0
_FAILURE_TTL_SECONDS = 5.0
_PROBE_TIMEOUT_SECONDS = 8.0
_MAX_PROBE_OUTPUT = 256 * 1024
_CACHE_LIMIT = 16
_CACHE: dict[tuple, tuple[float, frozenset[str], frozenset[str]]] = {}
_CACHE_LOCK = threading.Lock()
_INFLIGHT_LOCK = _GeventSemaphore(1) if _HAVE_GEVENT else threading.Lock()
_INFLIGHT: dict[tuple, object] = {}


def _calibre_debug_path(converter_path: str, binaries_dir: str = "") -> str:
    """Return the debug tool beside the active converter, never another install."""
    if "\x00" in converter_path or "\x00" in binaries_dir:
        return ""
    try:
        if converter_path:
            converter = Path(converter_path)
            if converter.parent != Path("."):
                binary_dir = converter.parent
            else:
                # The conversion worker can execute a bare name via PATH.
                # Resolve it the same way so the companion probe cannot come
                # from an unrelated configured binary directory.
                resolved_converter = shutil.which(converter_path)
                if not resolved_converter:
                    return ""
                binary_dir = Path(resolved_converter).parent
        elif binaries_dir:
            binary_dir = Path(binaries_dir)
        else:
            return ""

        name = "calibre-debug.exe" if os.name == "nt" else "calibre-debug"
        candidate = binary_dir / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
        return ""
    except (OSError, RuntimeError, ValueError):
        return ""


def _path_identity(path: Path) -> tuple:
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_mtime_ns, stat.st_size, stat.st_ino)
    except (OSError, RuntimeError, ValueError):
        return (str(path), None, None, None)


def _plugin_registry_identity(env: dict[str, str]) -> tuple:
    try:
        config_dir = env.get("CALIBRE_CONFIG_DIRECTORY")
        if config_dir:
            root = Path(config_dir)
        elif env.get("XDG_CONFIG_HOME"):
            root = Path(env["XDG_CONFIG_HOME"]) / "calibre"
        else:
            root = Path(env.get("HOME") or Path.home()) / ".config" / "calibre"
        root_identity = str(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return ("invalid-config-root", env.get("CALIBRE_CONFIG_DIRECTORY", ""))
    registry = root / "customize.py.json"
    plugin_dir = root / "plugins"
    identity: list[tuple] = [_path_identity(registry)]
    try:
        # Hash the small registry as well as its stat tuple: some mounted
        # filesystems preserve timestamps when the operator replaces it.
        with registry.open("rb") as handle:
            registry_bytes = handle.read(_MAX_PROBE_OUTPUT + 1)
        if len(registry_bytes) <= _MAX_PROBE_OUTPUT:
            identity.append((hashlib.sha256(registry_bytes).hexdigest(),))
        else:
            identity.append(("registry-too-large", _path_identity(registry)[2]))
    except (OSError, RuntimeError, ValueError):
        identity.append(("registry-missing",))
    # Installation/removal changes the plugin directory identity. In-place
    # archive edits are picked up by the 60-second refresh even when Calibre's
    # registry JSON remains unchanged.
    identity.append(("plugin-directory", *_path_identity(plugin_dir)[1:]))
    return (root_identity, *identity)


def _cache_key(debug_path: str, env: dict[str, str]) -> tuple:
    return (
        debug_path,
        _path_identity(Path(debug_path)),
        env.get("PATH", ""),
        env.get("HOME", ""),
        env.get("CALIBRE_CONFIG_DIRECTORY", ""),
        env.get("XDG_CONFIG_HOME", ""),
        env.get("CALIBRE_DEVELOP_FROM", ""),
        env.get("PYTHONPATH", ""),
        env.get("QT_PLUGIN_PATH", ""),
        env.get("CWA_CALIBRE_USER_PLUGINS", ""),
        _plugin_registry_identity(env),
    )


def _request_key(converter_path: str, binaries_dir: str, env: dict[str, str]) -> tuple:
    """Cheap key used only to coalesce simultaneous request-path probes."""
    return (
        converter_path,
        binaries_dir,
        env.get("PATH", ""),
        env.get("HOME", ""),
        env.get("CALIBRE_CONFIG_DIRECTORY", ""),
        env.get("XDG_CONFIG_HOME", ""),
        env.get("CWA_CALIBRE_USER_PLUGINS", ""),
    )


def _parse_probe(stdout: str) -> tuple[frozenset[str], frozenset[str]] | None:
    if not isinstance(stdout, str) or len(stdout) > _MAX_PROBE_OUTPUT:
        return None
    try:
        matching_lines = [line[len(_PROBE_MARKER):] for line in stdout.splitlines() if line.startswith(_PROBE_MARKER)]
        if len(matching_lines) != 1:
            return None
        payload = json.loads(matching_lines[0])
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    parsed = []
    for name in ("inputs", "outputs"):
        values = payload.get(name)
        if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
            return None
        formats = {value.strip().lower().lstrip(".") for value in values}
        if any(not value or not value.isascii() or not value.replace("-", "").replace("_", "").isalnum() for value in formats):
            return None
        parsed.append(frozenset(formats))
    return parsed[0], parsed[1]


def _run_bounded_probe(debug_path: str, env: dict[str, str]) -> tuple[int, str] | None:
    """Bound child output/runtime and clean up the owned POSIX process group.

    Nonblocking pipe reads avoid a background reader surviving when a helper
    inherits the pipe. Windows retains direct-child cleanup; a runtime without
    nonblocking pipe support fails closed instead of starting a blocking reader.
    """
    try:
        process = subprocess.Popen(
            [debug_path, "-c", _PROBE_CODE],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=(os.name != "nt"),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None

    output = bytearray()
    try:
        assert process.stdout is not None
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        deadline = time.monotonic() + _PROBE_TIMEOUT_SECONDS
        pipe_closed = False
        while time.monotonic() < deadline:
            if not pipe_closed:
                try:
                    chunk = os.read(descriptor, min(8192, _MAX_PROBE_OUTPUT + 1 - len(output)))
                except BlockingIOError:
                    chunk = None
                if chunk == b"":
                    pipe_closed = True
                elif chunk:
                    output.extend(chunk)
                    if len(output) > _MAX_PROBE_OUTPUT:
                        return None
                    # Drain available bytes promptly, checking the deadline
                    # between reads. Retained memory never exceeds MAX+1.
                    continue
            return_code = process.poll()
            if pipe_closed and return_code is not None:
                return return_code, output.decode("utf-8", errors="replace")
            time.sleep(0.02)
        return None
    except (OSError, ValueError, NotImplementedError):
        return None
    finally:
        if os.name != "nt":
            # The leader may already have exited while a helper retains its
            # pipe. Its private process group still belongs to this probe.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1)
        finally:
            if process.stdout is not None:
                process.stdout.close()


def _get_cached_capabilities(
    converter_path: str,
    binaries_dir: str,
    env: dict[str, str],
) -> tuple[frozenset[str], frozenset[str]]:
    """Probe/cache implementation run through ``parallel.run_blocking``."""
    debug_path = _calibre_debug_path(converter_path, binaries_dir)
    if not debug_path:
        return frozenset(), frozenset()

    key = _cache_key(debug_path, env)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1], cached[2]

    try:
        result = _run_bounded_probe(debug_path, env)
        capabilities = _parse_probe(result[1]) if result and result[0] == 0 else None
    except Exception:
        # This is an optional capability display/validation probe. Unexpected
        # local executable or filesystem failures must never
        # turn into unverified formats or a failed book-detail request.
        capabilities = None

    if capabilities is None:
        capabilities = (frozenset(), frozenset())
        ttl = _FAILURE_TTL_SECONDS
    else:
        ttl = _CACHE_TTL_SECONDS
    # A timed-out probe can outlast the entire failure TTL. Backoff begins
    # when the result is available, rather than expiring during startup.
    now = time.monotonic()
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_LIMIT:
            # Expired entries first, then remove the oldest expiry. No user- or
            # plugin-controlled input can grow this process cache unboundedly.
            expired = [cache_key for cache_key, value in _CACHE.items() if value[0] <= now]
            for cache_key in expired:
                _CACHE.pop(cache_key, None)
            while len(_CACHE) >= _CACHE_LIMIT:
                oldest = min(_CACHE, key=lambda cache_key: _CACHE[cache_key][0])
                _CACHE.pop(oldest, None)
        _CACHE[key] = (now + ttl, capabilities[0], capabilities[1])
    return capabilities


def _on_gevent_hub_thread() -> bool:
    if not _HAVE_GEVENT:
        return False
    try:
        return gevent.get_hub().thread_ident == threading.get_ident()
    except (RuntimeError, AttributeError):
        return False


def get_conversion_capabilities(
    converter_path: str,
    binaries_dir: str = "",
) -> tuple[frozenset[str], frozenset[str]]:
    """Return formats the configured Calibre install can read and write.

    File identity and subprocess work run in the shared bounded blocking pool,
    not on the request greenlet. Same-key requests share one cooperative
    in-flight result, while cache locks are never held during Calibre startup.
    """
    env = calibre_user_plugins.apply_to_env(os.environ.copy())
    if _on_gevent_hub_thread():
        request_key = _request_key(converter_path, binaries_dir, env)
        with _INFLIGHT_LOCK:
            pending = _INFLIGHT.get(request_key)
            if pending is None:
                pending = _GeventAsyncResult()
                _INFLIGHT[request_key] = pending
                owns_probe = True
            else:
                owns_probe = False
        if not owns_probe:
            try:
                return pending.get(timeout=_PROBE_TIMEOUT_SECONDS + 2)
            except gevent.Timeout:
                return frozenset(), frozenset()

        try:
            value = parallel.run_blocking(
                lambda: _get_cached_capabilities(converter_path, binaries_dir, env)
            )
        except BaseException:
            with _INFLIGHT_LOCK:
                _INFLIGHT.pop(request_key, None)
            pending.set((frozenset(), frozenset()))
            raise
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(request_key, None)
        pending.set(value)
        return value

    return parallel.run_blocking(
        lambda: _get_cached_capabilities(converter_path, binaries_dir, env)
    )


def clear_conversion_capability_cache() -> None:
    """Clear the bounded process cache; primarily useful to runtime callers/tests."""
    with _CACHE_LOCK:
        _CACHE.clear()
