# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency-free Calibre addressing policy shared by app and standalone tools."""
import ipaddress
import os
import time
import json
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path

LibraryTarget = namedtuple("LibraryTarget", "args stdin")


def library_id(library_dir):
    """calibre-server's id for a library: folder name, spaces as ``_``.

    Mirrors Calibre's ``library_id_from_path``; both app and scripts use
    this implementation.
    """
    return os.path.basename(str(library_dir).rstrip("/")).replace(" ", "_")


def connect_host(listen):
    """Where this host reaches the server; shared by app and scripts."""
    listen = (listen or "").strip()
    if listen in ("", "0.0.0.0"):
        return "127.0.0.1"
    if listen == "::":
        return "::1"
    try:
        return str(ipaddress.ip_address(listen))
    except ValueError:
        return "127.0.0.1"


def server_target(library_dir, enabled, port, listen, anonymous_writes, username, password,
                  is_answering, announce_fallback, owner_busy=None):
    """Shared app/script routing policy; an empty target means use the local path.

    Only the explicit anonymous choice can omit credentials. This policy is
    separate from loading Flask config or encrypted app.db values so both
    consumers use the same decision and argument boundaries.
    """
    if not enabled or not library_dir:
        path_is_available(owner_busy)
        return LibraryTarget([], None)
    if not anonymous_writes and not (username and password):
        path_is_available(owner_busy)
        announce_fallback("no content server credentials are configured")
        return LibraryTarget([], None)
    host = connect_host(listen)
    answering = is_answering(host, port)
    if answering and owner_busy is not None and not owner_busy():
        raise RuntimeError("Configured content-server port answers without managed ownership")
    if not answering and owner_busy is not None:
        deadline = time.monotonic() + 30
        while owner_busy():
            if time.monotonic() >= deadline:
                raise TimeoutError("Managed Calibre server owns the library but is not ready")
            time.sleep(0.1)
            answering = is_answering(host, port)
            if answering:
                break
    if not answering:
        path_is_available(owner_busy)
        announce_fallback("it is not answering on {} port {}".format(host, port))
        return LibraryTarget([], None)
    url_host = "[{}]".format(host) if ":" in host else host
    args = ["--with-library", "http://{}:{}/#{}".format(url_host, port, library_id(library_dir))]
    if anonymous_writes:
        return LibraryTarget(args, None)
    args += ["--username", username, "--password", "<stdin>"]
    return LibraryTarget(args, password + "\n")


def path_is_available(owner_busy):
    if owner_busy is not None and owner_busy():
        raise TimeoutError("Managed Calibre server still owns the library; retry after reconciliation")


def server_ready(host, port, library_dir, timeout=0.5):
    """Recognize Calibre without sending credentials to an arbitrary listener."""
    host = "[{}]".format(host) if ":" in host else host
    request = urllib.request.Request("http://{}:{}/ajax/library-info".format(host, int(port)))
    # Local service probes must bypass the deployment's outbound proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            if not response.headers.get("Server", "").startswith("calibre "):
                return False
            data = json.loads(response.read(65537))
            libraries = data.get("library_map") if isinstance(data, dict) else None
            return isinstance(libraries, dict) and library_id(library_dir) in libraries
    except urllib.error.HTTPError as error:
        try:
            server = error.headers.get("Server", "")
            # Calibre 9.11 omits Server on its real authentication error.
            # Match its challenge without offering credentials; routing also
            # requires the independent managed-process owner lock.
            return (error.code == 401
                    and (not server or server.startswith("calibre "))
                    and error.headers.get("WWW-Authenticate", "").startswith('Digest realm="calibre",'))
        finally:
            error.close()
    except (OSError, ValueError, TypeError):
        return False


def calibredb_command(command, target):
    """Preserve CLI arguments while preventing getpass from selecting a TTY."""
    if target.stdin is None:
        return command
    binary = Path(command[0])
    debug = binary.with_name("calibre-debug.exe" if binary.suffix.lower() == ".exe" else "calibre-debug")
    helper = Path(__file__).with_name("calibredb_pipe.py")
    return [str(debug), "-e", str(helper), "--", *command[1:]]
