# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded local OCR with source coordinates; no structural or prose editing.

This adapter does not decide whether a PDF text layer should be replaced. It
supplies recognition evidence to that decision, including every low-confidence
word. Coordinates use points: ``bbox`` is upright, ``source_bbox`` is the original
displayed PDF page, and ``pdf_bbox`` is that page's unrotated coordinate space.
Tesseract scores are engine scores, not probabilities of a correct transcription.
"""

from dataclasses import asdict, dataclass
from functools import lru_cache
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

import pymupdf

ADAPTER_VERSION = "1"
MAX_PIXELS = 12_000_000
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_WORDS = 100_000
LANGUAGE = re.compile(r"[A-Za-z0-9_]{1,32}\Z")


class OCRUnavailable(RuntimeError):
    pass


class OCRFailed(RuntimeError):
    pass


class OCRCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRWord:
    text: str
    bbox: tuple
    source_bbox: tuple
    pdf_bbox: tuple
    confidence: float
    block: int
    paragraph: int
    line: int


@dataclass(frozen=True)
class OCRResult:
    source_sha256: str
    page_index: int
    engine_version: str
    language: str
    language_identity: str
    requested_dpi: int
    effective_dpi: float
    source_rotation: int
    orientation_clockwise: int
    orientation_confidence: float
    width: float
    height: float
    words: tuple
    flags: tuple

    def to_dict(self):
        return asdict(self)


def _env():
    # The recognizer does not need the application's provider keys or DB secrets.
    environment = {name: os.environ[name] for name in
                   ("PATH", "LANG", "LC_ALL", "TESSDATA_PREFIX", "TMPDIR")
                   if name in os.environ}
    environment["OMP_THREAD_LIMIT"] = "1"
    return environment


@lru_cache(maxsize=32)
def _file_digest(path, size, modified_ns):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _engine(language):
    executable = shutil.which("tesseract")
    if not executable:
        raise OCRUnavailable("Local text recognition requires Tesseract.")
    requested = language.split("+") if isinstance(language, str) else []
    if not requested or len(requested) > 4 or any(not LANGUAGE.fullmatch(x) for x in requested):
        raise OCRUnavailable("Choose one to four installed OCR languages.")
    try:
        version = subprocess.run([executable, "--version"], capture_output=True,
                                 text=True, timeout=10, check=True, env=_env()).stdout.splitlines()[0]
        listing = subprocess.run([executable, "--list-langs"], capture_output=True,
                                 text=True, timeout=10, check=True, env=_env()).stdout
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise OCRUnavailable("The local text recognition engine is unavailable.") from exc
    lines = listing.splitlines()
    available = {line.strip() for line in lines[1:] if LANGUAGE.fullmatch(line.strip())}
    if any(name not in available or name == "osd" for name in requested):
        raise OCRUnavailable("The requested OCR language is not installed.")
    if "osd" not in available:
        raise OCRUnavailable("OCR orientation data (osd) is not installed.")
    match = re.search(r'"([^"\n]+)"', lines[0] if lines else "")
    data_dir = Path(os.environ.get("TESSDATA_PREFIX") or (match.group(1) if match else ""))
    identities = []
    try:
        for name in sorted(set(requested + ["osd"])):
            path = (data_dir / (name + ".traineddata")).resolve(strict=True)
            stat = path.stat()
            identities.append((name, _file_digest(str(path), stat.st_size, stat.st_mtime_ns)))
    except OSError as exc:
        raise OCRUnavailable("OCR language data could not be verified.") from exc
    identity = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
    return executable, version, identity


def _stopped(should_stop):
    if should_stop is not None and should_stop():
        raise OCRCancelled("Text recognition was cancelled.")


def _invoke(args, directory, *, timeout, should_stop, watched=()):
    """Bound both execution and disk output; always reap our child on failure."""
    stdout, stderr = directory / "stdout", directory / "stderr"
    started = time.monotonic()
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(args, stdout=out, stderr=err, env=_env())
        try:
            while True:
                _stopped(should_stop)
                if time.monotonic() - started >= timeout:
                    raise OCRFailed("Text recognition exceeded its time limit.")
                for path, limit in [(stdout, MAX_OUTPUT_BYTES), (stderr, 65536),
                                    *[(path, MAX_OUTPUT_BYTES) for path in watched]]:
                    if path.exists() and path.stat().st_size > limit:
                        raise OCRFailed("Text recognition exceeded its output limit.")
                try:
                    status = process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            # A short-lived process can finish between the checks above.
            if any(path.exists() and path.stat().st_size > limit for path, limit in
                   [(stdout, MAX_OUTPUT_BYTES), (stderr, 65536),
                    *[(path, MAX_OUTPUT_BYTES) for path in watched]]):
                raise OCRFailed("Text recognition exceeded its output limit.")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    return status, stdout.read_text(errors="replace"), stderr.read_text(errors="replace")


def _source_box(box, width, height, angle):
    def point(x, y):
        if angle == 90:
            return y, height - x
        if angle == 180:
            return width - x, height - y
        if angle == 270:
            return width - y, x
        return x, y
    points = [point(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
    return (max(0., min(x for x, _ in points)), max(0., min(y for _, y in points)),
            min(width, max(x for x, _ in points)), min(height, max(y for _, y in points)))


def _parse_words(path, zoom, source_rect, angle, derotation):
    words = []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t", quoting=csv.QUOTE_NONE)
        if not {"level", "text", "left", "top", "width", "height", "conf",
                "block_num", "par_num", "line_num"}.issubset(reader.fieldnames or ()):
            raise OCRFailed("The recognition engine returned an invalid word table.")
        try:
            for row in reader:
                if row["level"] != "5" or not row["text"].strip():
                    continue
                left, top, width, height = (int(row[name]) / zoom for name in
                                            ("left", "top", "width", "height"))
                score = float(row["conf"])
                upright_width = source_rect.height if angle % 180 else source_rect.width
                upright_height = source_rect.width if angle % 180 else source_rect.height
                if (not math.isfinite(score) or not 0 <= score <= 100 or
                        min(left, top, width, height) < 0 or
                        left + width > upright_width + 1 / zoom or
                        top + height > upright_height + 1 / zoom):
                    raise ValueError("invalid word geometry")
                box = (left, top, left + width, top + height)
                source = _source_box(box, source_rect.width, source_rect.height, angle)
                pdf = tuple(pymupdf.Rect(source) * derotation)
                words.append(OCRWord(row["text"], box, source, pdf, score,
                                     int(row["block_num"]), int(row["par_num"]), int(row["line_num"])))
                if len(words) > MAX_WORDS:
                    raise OCRFailed("Text recognition exceeded its word limit.")
        except (ValueError, TypeError, KeyError, AttributeError, csv.Error) as exc:
            raise OCRFailed("The recognition engine returned an invalid word table.") from exc
    return tuple(words)


def _load_cache(path, key, options):
    try:
        if path.stat().st_size > MAX_OUTPUT_BYTES:
            return None
        payload = json.loads(path.read_text())
        if payload["key"] != key:
            return None
        data = payload["result"]
        expected = dict(source_sha256=options["source"], page_index=options["page"],
                        engine_version=options["engine"], language=options["language"],
                        language_identity=options["data"], requested_dpi=options["dpi"],
                        source_rotation=options["rotation"])
        if any(data.get(name) != value for name, value in expected.items()):
            return None
        if data["orientation_clockwise"] not in (0, 90, 180, 270):
            return None
        for name in ("effective_dpi", "width", "height", "orientation_confidence"):
            value = data[name]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                return None
        if not 0 < data["effective_dpi"] <= options["dpi"] or min(data["width"], data["height"]) <= 0:
            return None
        if len(data["words"]) > MAX_WORDS or not isinstance(data["flags"], list):
            return None
        if any(flag not in ("resolution_limited", "orientation_uncertain", "no_text_detected",
                            "uncertain_words") for flag in data["flags"]):
            return None
        words = []
        for word in data["words"]:
            if not isinstance(word["text"], str) or not word["text"].strip():
                return None
            for name in ("bbox", "source_bbox", "pdf_bbox"):
                box = word[name]
                if len(box) != 4 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in box):
                    return None
                if box[0] > box[2] or box[1] > box[3]:
                    return None
                word[name] = tuple(box)
            if not isinstance(word["confidence"], (int, float)) or not 0 <= word["confidence"] <= 100:
                return None
            if any(not isinstance(word[name], int) or word[name] < 0
                   for name in ("block", "paragraph", "line")):
                return None
            words.append(OCRWord(**word))
        data["words"], data["flags"] = tuple(words), tuple(data["flags"])
        return OCRResult(**data)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _save_cache(path, key, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"key": key, "result": result.to_dict()}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def recognize_page(page, *, source_sha256, language="eng", dpi=300,
                   max_pixels=MAX_PIXELS, timeout=60, cache_dir=None,
                   scratch_dir=None, should_stop=None):
    """Recover words and source coordinates without altering the PDF or its layer.

    ``source_sha256`` must describe the original PDF bytes; callers compute it once
    per document. A low-resolution downgrade and uncertain orientation are explicit.
    The caller retains responsibility for prose/table layout and source-layer audit.
    """
    _stopped(should_stop)
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("A SHA-256 identity of the source PDF is required.")
    if not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise ValueError("OCR resolution must be between 72 and 600 dpi.")
    if not isinstance(max_pixels, int) or not 1 <= max_pixels <= MAX_PIXELS:
        raise ValueError("The OCR pixel limit is out of range.")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("The OCR time limit is out of range.")
    rect = page.rect
    if not all(math.isfinite(x) for x in rect) or min(rect.width, rect.height) <= 0:
        raise OCRFailed("The source page has invalid dimensions.")
    executable, version, data_identity = _engine(language)
    options = dict(adapter=ADAPTER_VERSION, source=source_sha256, page=page.number,
                   rect=tuple(rect), rotation=page.rotation, engine=version,
                   language=language, data=data_identity, dpi=dpi, max_pixels=max_pixels)
    key = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()
    cache_path = Path(cache_dir) / (key + ".json") if cache_dir else None
    if cache_path:
        cached = _load_cache(cache_path, key, options)
        if cached is not None:
            _stopped(should_stop)
            return cached
    zoom = min(dpi / 72., math.sqrt(max_pixels / (rect.width * rect.height)))
    # MuPDF rounds the raster outward. Include that rounding before allocation.
    while math.ceil(rect.width * zoom) * math.ceil(rect.height * zoom) > max_pixels:
        zoom *= 0.99
    if min(rect.width * zoom, rect.height * zoom) < 1:
        raise OCRFailed("The page cannot be recognized within the raster limit.")
    flags = [] if zoom == dpi / 72. else ["resolution_limited"]
    with tempfile.TemporaryDirectory(prefix="reflow-ocr-", dir=scratch_dir) as temporary:
        directory = Path(temporary)
        image = directory / "page.jpg"
        _stopped(should_stop)
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(str(image))
        status, output, error = _invoke(
            [executable, str(image), "stdout", "-l", "osd", "--psm", "0"], directory,
            timeout=timeout, should_stop=should_stop)
        angle_match = re.search(r"^Rotate:\s*(0|90|180|270)\s*$", output, re.M)
        confidence_match = re.search(r"^Orientation confidence:\s*([\d.]+)", output, re.M)
        confidence = float(confidence_match.group(1)) if confidence_match else 0.
        angle = int(angle_match.group(1)) if status == 0 and angle_match and confidence >= 10 else 0
        if not angle_match or confidence < 10:
            flags.append("orientation_uncertain")
        if status != 0 and "Too few characters" not in error and "Too few characters" not in output:
            raise OCRFailed("The recognition engine could not determine page orientation.")
        if angle:
            _stopped(should_stop)
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom).prerotate(angle), alpha=False).save(str(image))
        table = directory / "result.tsv"
        status, _, _ = _invoke(
            [executable, str(image), str(directory / "result"), "-l", language,
             "--dpi", str(round(zoom * 72)), "--psm", "3", "tsv"], directory,
            timeout=timeout, should_stop=should_stop, watched=(table,))
        if status != 0 or not table.exists():
            raise OCRFailed("The recognition engine could not read the source page.")
        words = _parse_words(table, zoom, rect, angle, page.derotation_matrix)
    if not words:
        flags.append("no_text_detected")
    if any(word.confidence < 85 for word in words):
        flags.append("uncertain_words")
    result = OCRResult(source_sha256, page.number, version, language, data_identity, dpi,
                       zoom * 72, page.rotation, angle, confidence,
                       rect.height if angle % 180 else rect.width,
                       rect.width if angle % 180 else rect.height, words, tuple(flags))
    _stopped(should_stop)
    if cache_path:
        _save_cache(cache_path, key, result)
    return result
