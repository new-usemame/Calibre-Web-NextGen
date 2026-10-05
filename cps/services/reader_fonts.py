# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Instance-wide uploaded web-reader fonts.

The catalog lives below CONFIG_DIR so uploads survive image replacement. Each
immutable UUID directory contains one font and its small metadata record; the
whole directory is published by rename while holding a portable catalog lock.
Font parsing happens in Calibre's isolated Python, not the app interpreter (the
application deliberately has no fontTools dependency).
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import uuid
from typing import BinaryIO

from werkzeug.utils import secure_filename

from .. import config, constants, logger
from . import file_lock

log = logger.create()

MAX_FONT_FILE_BYTES = 8 * 1024 * 1024
MAX_FONT_COUNT = 32
MAX_FONT_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FONT_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_FONT_TABLES = 128
VALIDATOR_TIMEOUT_SECONDS = 10
VALIDATOR_CPU_SECONDS = 5
VALIDATOR_MEMORY_BYTES = 768 * 1024 * 1024
_MAX_VALIDATOR_OUTPUT_BYTES = 64 * 1024
_ROOT_NAME = "reader-fonts"
_LOCK_NAME = ".catalog.lock"
_RESULT_PREFIX = "CWNG_READER_FONT_RESULT="

FONT_FORMATS = {
    ".woff2": {"format": "woff2", "mime": "font/woff2", "signature": b"wOF2"},
    ".woff": {"format": "woff", "mime": "font/woff", "signature": b"wOFF"},
    ".ttf": {"format": "truetype", "mime": "font/ttf", "signature": (b"\x00\x01\x00\x00", b"true")},
    ".otf": {"format": "opentype", "mime": "font/otf", "signature": b"OTTO"},
}

# This constant program runs only in Calibre's packaged Python. It accepts its
# input path as an argv item, never interpolates request data into code,
# requires real naming and glyph tables, and forces all tables to decompress.
_VALIDATOR_CODE = r'''
import json, os, resource, sys
resource.setrlimit(resource.RLIMIT_CPU, (__CPU__, __CPU__ + 1))
resource.setrlimit(resource.RLIMIT_AS, (__MEMORY__, __MEMORY__))
resource.setrlimit(resource.RLIMIT_FSIZE, (__OUTPUT__, __OUTPUT__))
try:
    from fontTools.ttLib import TTFont
    path = sys.argv[-1]
    font = TTFont(path, lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    tags = [tag for tag in font.keys() if tag != "GlyphOrder"]
    if len(tags) > __TABLES__:
        raise ValueError("font has too many tables")
    required = {"head", "maxp", "cmap", "name"}
    if not required.issubset(set(tags)):
        raise ValueError("font is missing required tables")
    font.ensureDecompiled(recurse=True)
    if int(font["maxp"].numGlyphs) < 2:
        raise ValueError("font has no usable glyphs")
    names = font["name"]
    family = names.getDebugName(16) or names.getDebugName(1)
    style = names.getDebugName(17) or names.getDebugName(2)
    if not family or not family.strip() or not style or not style.strip():
        raise ValueError("font is missing family or style metadata")
    cmap = font.getBestCmap() or {}
    if not any(glyph and glyph != ".notdef" for glyph in cmap.values()):
        raise ValueError("font has no usable character map")
    # Calibre -c executes with distinct globals/locals. A generator expression
    # cannot resolve the top-level local `font`; keep table access in this scope.
    expanded = 0
    for tag in tags:
        expanded += len(font.getTableData(tag))
    if expanded > __UNCOMPRESSED__:
        raise ValueError("font expands beyond the supported limit")
    print("CWNG_READER_FONT_RESULT=" + json.dumps({
        "ok": True, "family": family.strip(), "style": style.strip(),
        "bytes_uncompressed": expanded,
    }, ensure_ascii=True))
except Exception:
    print("CWNG_READER_FONT_RESULT=" + json.dumps({
        "ok": False, "error": "invalid_font",
    }, ensure_ascii=True))
    sys.exit(2)
'''
_VALIDATOR_CODE = (_VALIDATOR_CODE
                   .replace("__CPU__", str(VALIDATOR_CPU_SECONDS))
                   .replace("__MEMORY__", str(VALIDATOR_MEMORY_BYTES))
                   .replace("__OUTPUT__", str(_MAX_VALIDATOR_OUTPUT_BYTES))
                   .replace("__TABLES__", str(MAX_FONT_TABLES))
                   .replace("__UNCOMPRESSED__", str(MAX_FONT_UNCOMPRESSED_BYTES)))


class ReaderFontError(ValueError):
    """An upload or catalog operation the caller can safely report."""


class ReaderFontValidatorUnavailable(RuntimeError):
    """The shipped Calibre parser could not be located or started."""


def root_dir() -> Path:
    """Persistent root for instance-wide uploaded reader fonts."""
    return Path(constants.CONFIG_DIR) / _ROOT_NAME


def _catalog_lock():
    root = root_dir()
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    handle = open(root / _LOCK_NAME, "a+b")
    try:
        file_lock.acquire(handle.fileno())
    except Exception:
        handle.close()
        raise
    return handle


@contextmanager
def catalog_lock():
    handle = _catalog_lock()
    try:
        yield
    finally:
        try:
            file_lock.release(handle.fileno())
        finally:
            handle.close()


def _safe_uuid(value: str | uuid.UUID) -> str | None:
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
    canonical = str(parsed)
    if isinstance(value, str) and canonical != value.lower():
        return None
    return canonical


def _entry_path(font_id: str | uuid.UUID) -> Path | None:
    canonical = _safe_uuid(font_id)
    return root_dir() / canonical if canonical else None


def _read_record(path: Path) -> dict | None:
    if not path.is_dir() or path.is_symlink():
        return None
    metadata = path / "font.json"
    if metadata.is_symlink() or not metadata.is_file():
        return None
    try:
        record = json.loads(metadata.read_text(encoding="utf-8"))
        canonical = _safe_uuid(record.get("id", ""))
        extension = str(record.get("extension", "")).lower()
        font_file = path / ("font" + extension)
        if (canonical != path.name or extension not in FONT_FORMATS
                or font_file.is_symlink() or not font_file.is_file()):
            return None
        size = font_file.stat().st_size
        if size <= 0 or size > MAX_FONT_FILE_BYTES or size != record.get("size"):
            return None
        label = record.get("label")
        family = record.get("family")
        digest = record.get("sha256")
        if (not isinstance(label, str) or not label or not isinstance(family, str)
                or not re.fullmatch(r"CWNGUpload_[a-f0-9]{32}", family)
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or not isinstance(record.get("bytes_uncompressed"), int)
                or isinstance(record.get("bytes_uncompressed"), bool)
                or not 0 <= record["bytes_uncompressed"] <= MAX_FONT_UNCOMPRESSED_BYTES):
            return None
        if len(label) > 80 or any(not char.isprintable() for char in label):
            return None
        return {**record, "path": font_file}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _records_locked() -> list[dict]:
    root = root_dir()
    try:
        children = list(root.iterdir())
    except FileNotFoundError:
        return []
    records = []
    for child in children:
        if child.name.startswith((".staging-", ".deleted-")):
            if child.is_symlink() or not child.is_dir():
                try:
                    child.unlink()
                except OSError:
                    pass
            else:
                shutil.rmtree(child, ignore_errors=True)
            continue
        if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
            continue
        record = _read_record(child)
        if record:
            records.append(record)
    return records


def get_record(font_id: str | uuid.UUID) -> dict | None:
    path = _entry_path(font_id)
    if path is None or not root_dir().is_dir():
        return None
    with catalog_lock():
        return _read_record(path)


def exists(font_id: str | uuid.UUID) -> bool:
    return get_record(font_id) is not None


def custom_font_ids() -> set[str]:
    if not root_dir().is_dir():
        return set()
    with catalog_lock():
        return {"custom:" + record["id"] for record in _records_locked()}


def font_option(record: dict, url_for_font=None) -> dict:
    """Serialize one validated catalog record without a second catalog read."""
    fmt = FONT_FORMATS[record["extension"]]
    item = {
        "id": "custom:" + record["id"],
        "label": record["label"],
        "family": record["family"],
        "builtin": False,
        "format": fmt["format"],
        "mime": fmt["mime"],
    }
    if url_for_font:
        item["url"] = url_for_font(uuid.UUID(record["id"]))
    return item


BUILTIN_FONTS = (
    {"id": "default", "label": "Book default", "family": "initial", "builtin": True},
    {"id": "Literata", "label": "Literata", "family": "'Literata', serif", "builtin": True},
    {"id": "Arial", "label": "Arial", "family": "Arial, Helvetica, sans-serif", "builtin": True},
    {"id": "Yahei", "label": "Microsoft YaHei", "family": '"Microsoft YaHei", sans-serif', "builtin": True},
    {"id": "SimSun", "label": "SimSun", "family": "SimSun, serif", "builtin": True},
    {"id": "KaiTi", "label": "KaiTi", "family": "KaiTi, serif", "builtin": True},
)


def catalogue(url_for_font=None) -> dict:
    """Return built-ins plus currently usable uploads and fixed upload limits."""
    if root_dir().is_dir():
        with catalog_lock():
            records = _records_locked()
    else:
        records = []
    items = [dict(option) for option in BUILTIN_FONTS]
    for record in sorted(records, key=lambda row: row["label"].casefold()):
        items.append(font_option(record, url_for_font))
    total_bytes = sum(row["size"] for row in records)
    return {
        "items": items,
        "limits": {
            "max_file_bytes": MAX_FONT_FILE_BYTES,
            "max_fonts": MAX_FONT_COUNT,
            "max_total_bytes": MAX_FONT_TOTAL_BYTES,
            "used_fonts": len(records),
            "used_bytes": total_bytes,
        },
    }


def _read_limited(stream: BinaryIO, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ReaderFontError("Font file is larger than the 8 MiB limit")
    return b"".join(chunks)


def _format_for_upload(filename: str, payload: bytes) -> tuple[str, dict]:
    extension = Path(filename or "").suffix.lower()
    fmt = FONT_FORMATS.get(extension)
    if not fmt:
        raise ReaderFontError("Upload a WOFF2, WOFF, TTF, or OTF font file")
    signature = fmt["signature"]
    signatures = signature if isinstance(signature, tuple) else (signature,)
    if len(payload) < 4 or not any(payload.startswith(value) for value in signatures):
        raise ReaderFontError("The file contents do not match the font extension")
    return extension, fmt


def _display_label(value: str | None, filename: str, parsed_family: str) -> str:
    candidate = value if isinstance(value, str) and value.strip() else parsed_family
    if not candidate:
        candidate = Path(secure_filename(filename or "" )).stem
    label = " ".join("".join(c for c in candidate if c.isprintable()).split())[:80]
    if not label:
        label = "Uploaded font"
    return label


def _calibre_debug_path() -> str:
    from . import cover_generator
    return cover_generator.calibre_debug_path(getattr(config, "config_binariesdir", "") or "")


def _parse_with_calibre(path: str) -> dict:
    binary = _calibre_debug_path()
    if not binary:
        raise ReaderFontValidatorUnavailable("The Calibre font validator is unavailable")
    if os.name != "posix":
        raise ReaderFontValidatorUnavailable("Resource-limited font validation requires POSIX")
    env = os.environ.copy()
    stdout_file = tempfile.TemporaryFile()
    stderr_file = tempfile.TemporaryFile()
    process = None
    try:
        process = subprocess.Popen(
            [binary, "-c", _VALIDATOR_CODE, "--", path],
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=env,
            close_fds=True,
            start_new_session=(os.name == "posix"),
        )
        try:
            process.wait(timeout=VALIDATOR_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
            else:
                process.kill()
            process.wait()
            raise ReaderFontError("Font validation exceeded the time limit") from exc
        stdout_file.seek(0)
        output = stdout_file.read(_MAX_VALIDATOR_OUTPUT_BYTES + 1)
        if len(output) > _MAX_VALIDATOR_OUTPUT_BYTES:
            raise ReaderFontError("Font validator returned too much output")
        results = [line[len(_RESULT_PREFIX):] for line in output.decode("utf-8", "replace").splitlines()
                   if line.startswith(_RESULT_PREFIX)]
        if not results:
            if process.returncode in (-signal.SIGXCPU, -signal.SIGKILL, 128 + signal.SIGXCPU):
                raise ReaderFontError("Font validation exceeded its resource limit")
            raise ReaderFontError("This file could not be read as a supported font")
        try:
            result = json.loads(results[-1])
        except (TypeError, ValueError) as exc:
            raise ReaderFontError("The font validator returned an invalid result") from exc
        if not result.get("ok") or process.returncode != 0:
            raise ReaderFontError("This file is not a usable font")
        family = result.get("family")
        style = result.get("style")
        expanded = result.get("bytes_uncompressed")
        if (not isinstance(family, str) or not family.strip()
                or not isinstance(style, str) or not style.strip()
                or not isinstance(expanded, int) or expanded > MAX_FONT_UNCOMPRESSED_BYTES):
            raise ReaderFontError("The font has invalid family, style, or size metadata")
        return {"family": family.strip(), "style": style.strip(), "bytes_uncompressed": expanded}
    except OSError as exc:
        raise ReaderFontValidatorUnavailable("The Calibre font validator could not start") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        stdout_file.close()
        stderr_file.close()


def validate_font_bytes(payload: bytes, extension: str) -> dict:
    """Fully parse an allowed font through the shipped Calibre Python."""
    fmt = FONT_FORMATS.get(extension)
    if not fmt or len(payload) > MAX_FONT_FILE_BYTES:
        raise ReaderFontError("Unsupported or oversized font file")
    signature = fmt["signature"]
    signatures = signature if isinstance(signature, tuple) else (signature,)
    if len(payload) < 4 or not any(payload.startswith(value) for value in signatures):
        raise ReaderFontError("The file contents do not match the font extension")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="cwng-reader-font-", suffix=extension, delete=False) as handle:
            temp_path = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return _parse_with_calibre(temp_path)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_atomic(path: Path, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _check_capacity(records: list[dict], incoming_size: int) -> None:
    if len(records) >= MAX_FONT_COUNT:
        raise ReaderFontError("The reader font limit has been reached")
    used_bytes = sum(row["size"] for row in records)
    if used_bytes + incoming_size > MAX_FONT_TOTAL_BYTES:
        raise ReaderFontError("The reader font storage limit has been reached")


def upload_font(stream: BinaryIO, filename: str, label: str | None = None) -> tuple[dict, bool]:
    """Validate and publish one immutable font; return (record, created)."""
    payload = _read_limited(stream, MAX_FONT_FILE_BYTES)
    extension, _fmt = _format_for_upload(filename, payload)
    digest = hashlib.sha256(payload).hexdigest()
    root = root_dir()
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    # Avoid both redundant parsing for a duplicate and expensive parser work
    # when the bounded catalog is already full. Recheck after parsing because
    # another admin may have published while this upload was being validated.
    with catalog_lock():
        records = _records_locked()
        duplicate = next((row for row in records if row["sha256"] == digest), None)
        if duplicate:
            return duplicate, False
        _check_capacity(records, len(payload))
    parsed = validate_font_bytes(payload, extension)
    safe_name = secure_filename(filename or "") or "font" + extension
    display_label = _display_label(label, safe_name, parsed["family"])
    stage = None
    try:
        with catalog_lock():
            records = _records_locked()
            duplicate = next((row for row in records if row["sha256"] == digest), None)
            if duplicate:
                return duplicate, False
            _check_capacity(records, len(payload))
            font_id = str(uuid.uuid4())
            stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
            target_file = stage / ("font" + extension)
            with open(target_file, "xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            record = {
                "id": font_id,
                "label": display_label,
                "family": "CWNGUpload_" + font_id.replace("-", ""),
                "extension": extension,
                "sha256": digest,
                "size": len(payload),
                "bytes_uncompressed": parsed["bytes_uncompressed"],
            }
            _write_json_atomic(stage / "font.json", record)
            _sync_directory(stage)
            target = root / font_id
            os.replace(stage, target)
            stage = None
            _sync_directory(root)
            record["path"] = target / ("font" + extension)
            return record, True
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def delete_font(font_id: str | uuid.UUID) -> bool:
    """Atomically remove an entry from the catalog; IDs are never reused."""
    path = _entry_path(font_id)
    if path is None:
        return False
    tombstone = None
    with catalog_lock():
        if not _read_record(path):
            return False
        tombstone = root_dir() / (".deleted-" + path.name + "-" + uuid.uuid4().hex)
        os.replace(path, tombstone)
        _sync_directory(root_dir())
    shutil.rmtree(tombstone, ignore_errors=True)
    return True


def file_for_font(font_id: str | uuid.UUID) -> dict | None:
    """Return metadata and the validated immutable file path for delivery."""
    return get_record(font_id)


def open_file_for_font(font_id: str | uuid.UUID):
    """Open the immutable file while locked so deletion cannot race its open."""
    path = _entry_path(font_id)
    if path is None or not root_dir().is_dir():
        return None
    with catalog_lock():
        record = _read_record(path)
        if not record:
            return None
        try:
            stream = open(record["path"], "rb")
        except OSError:
            return None
    return record, stream
