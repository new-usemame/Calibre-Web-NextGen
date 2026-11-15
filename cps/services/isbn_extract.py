# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, read-only ISBN-13 extraction from a book's existing local formats."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from .. import config

MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_FORMATS = 6
WORKER_TIMEOUT_SECONDS = 10
MAX_WORKER_OUTPUT_BYTES = 64 * 1024
SUPPORTED_FORMATS = {"EPUB", "KEPUB", "PDF", "TXT"}
_WORKER_PATH = Path(__file__).with_name("isbn_extract_worker.py")


class ISBNExtractionError(Exception):
    """A safe, stable failure category for the API to render."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _inside_root(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath((root, candidate)) == root
    except ValueError:
        return False


def _open_book_format(book, data_entry, root: str) -> int:
    """Open only an existing regular library file, retaining the checked FD.

    The worker receives this descriptor instead of reopening a path, so a
    symlink/path replacement after validation cannot redirect the parser.
    """
    book_path = getattr(book, "path", None)
    name = getattr(data_entry, "name", None)
    fmt = str(getattr(data_entry, "format", "") or "").lower()
    normalized_book_path = book_path.replace("\\", "/") if isinstance(book_path, str) else ""
    if (not normalized_book_path or normalized_book_path.startswith("/")
            or any(part == ".." for part in normalized_book_path.split("/"))
            or not isinstance(name, str) or not name or "/" in name or "\\" in name
            or name in {".", ".."}):
        raise ISBNExtractionError("unsafe_book_path")
    candidate = os.path.join(root, book_path, f"{name}.{fmt}")
    resolved = os.path.realpath(candidate)
    if not _inside_root(root, resolved):
        raise ISBNExtractionError("unsafe_book_path")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(candidate, flags)
    except OSError:
        raise ISBNExtractionError("file_unavailable") from None
    try:
        opened = os.fstat(fd)
        current = os.stat(candidate)
        current_real = os.path.realpath(candidate)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size < 0
                or opened.st_size > MAX_INPUT_BYTES or not _inside_root(root, current_real)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise ISBNExtractionError("file_unavailable")
        return fd
    except Exception:
        os.close(fd)
        raise


def _ordered_formats(book):
    formats = []
    priority = {"EPUB": 0, "KEPUB": 1, "PDF": 2, "TXT": 3}
    for entry in list(getattr(book, "data", None) or []):
        fmt = str(getattr(entry, "format", "") or "").upper()
        if fmt in SUPPORTED_FORMATS:
            formats.append((priority[fmt], fmt, entry))
    formats.sort(key=lambda item: (item[0], item[1]))
    return formats


def extract_candidates(book):
    """Scan bounded supported formats; never mutates book metadata.

    Returns an API-ready result. Unsupported/missing formats are explicit so
    the editor can distinguish "nothing found" from "nothing was scanned".
    """
    if getattr(config, "config_use_google_drive", False):
        raise ISBNExtractionError("unsupported_storage")
    # The parser worker requires enforceable CPU and address-space rlimits.
    # macOS exposes similarly named constants but does not allow this process
    # to install the required address-space ceiling; never parse without it.
    if not sys.platform.startswith("linux"):
        raise ISBNExtractionError("unsupported_platform")

    root = os.path.realpath(config.get_book_path())
    formats = _ordered_formats(book)
    unsupported = sorted({
        str(getattr(entry, "format", "") or "").upper()
        for entry in list(getattr(book, "data", None) or [])
        if str(getattr(entry, "format", "") or "").upper() not in SUPPORTED_FORMATS
    })
    unavailable = []
    opened = []
    truncated = False
    try:
        for _priority, fmt, entry in formats[:MAX_FORMATS]:
            try:
                fd = _open_book_format(book, entry, root)
            except ISBNExtractionError as exc:
                if exc.code == "unsafe_book_path":
                    raise
                unavailable.append(fmt)
                continue
            opened.append({"fd": fd, "format": fmt})
        if len(formats) > MAX_FORMATS:
            truncated = True
        if not opened:
            return {
                "available": False,
                "candidates": [],
                "scanned_formats": [],
                "unsupported_formats": unsupported,
                "unavailable_formats": sorted(set(unavailable)),
                "failed_formats": [],
                "truncated": truncated,
            }
        result = _run_worker(opened)
        result["unsupported_formats"] = unsupported
        result["unavailable_formats"] = sorted(set(unavailable))
        result["truncated"] = bool(result.get("truncated")) or truncated
        return result
    finally:
        for item in opened:
            try:
                os.close(item["fd"])
            except OSError:
                pass


def _run_worker(formats):
    """Run parsers in a resource-limited child with only validated FDs."""
    if not sys.platform.startswith("linux"):
        raise ISBNExtractionError("unsupported_platform")
    pass_fds = tuple(item["fd"] for item in formats)
    payload = json.dumps({
        "formats": formats,
        "max_input_bytes": MAX_INPUT_BYTES,
    }, separators=(",", ":")).encode("ascii")
    env = {"PYTHONIOENCODING": "utf-8"}
    try:
        completed = subprocess.run(
            [sys.executable, str(_WORKER_PATH)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=WORKER_TIMEOUT_SECONDS,
            check=False,
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=(os.name == "posix"),
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise ISBNExtractionError("scan_timeout") from None
    except OSError:
        raise ISBNExtractionError("scan_unavailable") from None
    if completed.returncode != 0 or len(completed.stdout) > MAX_WORKER_OUTPUT_BYTES:
        raise ISBNExtractionError("scan_failed")
    try:
        result = json.loads(completed.stdout)
    except (ValueError, UnicodeError, TypeError):
        raise ISBNExtractionError("scan_failed") from None
    if isinstance(result, dict) and result.get("sandboxed") is False:
        raise ISBNExtractionError("sandbox_unavailable")
    if (not isinstance(result, dict) or not isinstance(result.get("candidates"), list)
            or result.get("sandboxed") is not True):
        raise ISBNExtractionError("scan_failed")
    return result
