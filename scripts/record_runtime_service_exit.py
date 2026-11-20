#!/usr/bin/env python3
"""Record one s6 longrun exit without importing the application package.

This stays stdlib-only because it is invoked after the application process has
already exited.  Its output directory and record name are constants owned by the
image; supervisor values are numeric, so no user-controlled path or log content
is written during failure handling.
"""

import json
import os
import re
import sys
import tempfile
import time


SUPERVISION_DIR = "/config/.runtime-supervision"
RECORD_NAME = "svc-calibre-web-automated-last-exit.json"
_EXIT_CODE = re.compile(r"^-?[0-9]+$")
_SIGNAL = re.compile(r"^[0-9]+$")


def _number(value, name):
    matcher = _EXIT_CODE if name == "exit code" else _SIGNAL
    if not matcher.match(str(value or "")):
        expectation = "an integer" if name == "exit code" else "a non-negative integer"
        raise ValueError("%s must be %s" % (name, expectation))
    return int(value)


def record_exit(directory, exit_code, signal, now=None):
    """Atomically replace the service's last-exit record in an owned directory."""
    exit_code = _number(exit_code, "exit code")
    signal = _number(signal, "signal")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    payload = {"schema": 1, "service": "svc-calibre-web-automated",
               "exit_code": exit_code, "signal": signal,
               "recorded_at": int(time.time() if now is None else now)}
    fd, temporary = tempfile.mkstemp(prefix=".svc-calibre-web-automated-",
                                     suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, os.path.join(directory, RECORD_NAME))
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return payload


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        raise SystemExit("usage: record_runtime_service_exit.py EXIT_CODE SIGNAL")
    record_exit(SUPERVISION_DIR, argv[0], argv[1])


if __name__ == "__main__":
    main()
