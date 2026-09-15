# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional, read-only Storyteller reading-position adapter.

Connections are server-administered and assigned to a specific CWNG user.  A
shared token would leak one household member's reading activity to another, so
there is intentionally no global-token fallback.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import quote, unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import requests


REQUEST_TIMEOUT = (2, 5)
MAX_CONFIG_BYTES = 128 * 1024


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize(value):
    text = unicodedata.normalize("NFKD", value or "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _title_matches(local, remote):
    left, right = _normalize(local), _normalize(remote)
    return left == right or right.startswith(left + " ") or left.startswith(right + " ")


def _author_matches(local_authors, remote):
    wanted = {_normalize(author) for author in local_authors if _normalize(author)}
    found = {_normalize(row.get("name")) for row in remote.get("authors", [])}
    return not wanted or bool(wanted & found)


class StorytellerClient:
    def __init__(self, base_url, token, *, session=None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Storyteller URL must be absolute HTTP(S)")
        if not token:
            raise ValueError("Storyteller token is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = session or requests.Session()

    def _request(self, path, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = "Bearer {}".format(self.token)
        response = self.session.get(
            self.base_url + path, headers=headers, timeout=REQUEST_TIMEOUT,
            allow_redirects=False, **kwargs,
        )
        response.raise_for_status()
        return response

    def get_json(self, path):
        return self._request(path).json()

    def get(self, path, **kwargs):
        return self._request(path, **kwargs)


def _connections_payload():
    path = os.environ.get("CWNG_STORYTELLER_CONNECTIONS_FILE")
    if path:
        config_path = Path(path)
        if config_path.stat().st_size > MAX_CONFIG_BYTES:
            raise ValueError("Storyteller connection file is too large")
        return json.loads(config_path.read_text(encoding="utf-8"))
    raw = os.environ.get("CWNG_STORYTELLER_CONNECTIONS_JSON")
    return json.loads(raw) if raw else {}


def configured_client(user_id):
    """Resolve only this account's admin-configured connection."""
    try:
        row = _connections_payload().get(str(int(user_id)))
        if not isinstance(row, dict):
            return None
        return StorytellerClient(row.get("url", ""), row.get("token", ""))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _position_percent(locator):
    value = (locator.get("locations") or {}).get("totalProgression")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(100.0, float(value) * 100.0))


def _href_target(locator):
    href = str(locator.get("href") or "").lstrip("/")
    fragments = (locator.get("locations") or {}).get("fragments") or []
    if not href:
        return None
    if fragments and isinstance(fragments[0], str) and fragments[0]:
        return "{}#{}".format(href.split("#", 1)[0], quote(fragments[0], safe="-._~"))
    return href


def _archive_has_target(epub_path, target):
    """Return whether an EPUB target is exact enough for epub.js to open.

    Readium navigators may report synthetic fragment names (for example,
    ``chapter001-s352``) that do not exist as element ids in the publication.
    An identical archive therefore proves the edition, but not that another
    renderer can resolve that navigator-private fragment.  Treat those as an
    approximate percentage resume instead of claiming an exact place.
    """
    if not target or "#" not in target:
        return False
    href, fragment = target.split("#", 1)
    href = unquote(href).lstrip("/")
    fragment = unquote(fragment)
    if not href or not fragment or any(part == ".." for part in Path(href).parts):
        return False
    try:
        with ZipFile(epub_path) as archive:
            raw = archive.read(href)
    except (BadZipFile, KeyError, OSError):
        return False
    # XHTML ids are XML names, so checking the quoted attribute is both more
    # tolerant of imperfect publications and cheaper than parsing the document.
    escaped = re.escape(fragment.encode("utf-8"))
    return re.search(rb"\bid\s*=\s*(['\"])" + escaped + rb"\1", raw) is not None


def read_source(client, *, title, authors, epub_path):
    """Read one source; exact navigation is admitted only for identical EPUBs."""
    candidates = [
        row for row in client.get_json("/api/books")
        if _title_matches(title, row.get("title")) and _author_matches(authors, row)
    ]
    if len(candidates) != 1:
        return None
    book_id = str(candidates[0]["id"])
    book_path_id = quote(book_id, safe="")
    if not book_path_id:
        return None
    position = client.get_json("/api/books/{}/positions".format(book_path_id))
    locator = position.get("locator") or {}
    percentage = _position_percent(locator)
    if percentage is None:
        return None

    remote_hash = None
    try:
        response = client.get(
            "/api/v2/books/{}/files?format=ebook".format(book_path_id),
            headers={"Range": "bytes=0-0"},
        )
        if response.status_code in (200, 206):
            remote_hash = response.headers.get("X-Storyteller-Hash")
    except requests.RequestException:
        pass
    local_hash = sha256_file(epub_path)
    same_edition = bool(remote_hash and remote_hash == local_hash)
    target = _href_target(locator) if same_edition else None
    if target and not _archive_has_target(epub_path, target):
        target = None
    timestamp = position.get("timestamp")
    observed_at = None
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        observed_at = datetime.fromtimestamp(
            timestamp / 1000.0, tz=timezone.utc,
        ).isoformat()

    resume = {"percentage": percentage, "exact": bool(target)}
    if target:
        resume.update({"href": target, "epub_sha256": local_hash})
    elif same_edition:
        chapter_href = str(locator.get("href") or "").lstrip("/").split("#", 1)[0]
        chapter_progression = (locator.get("locations") or {}).get("progression")
        if (chapter_href and isinstance(chapter_progression, (int, float))
                and not isinstance(chapter_progression, bool)
                and 0.0 <= float(chapter_progression) <= 1.0):
            resume.update({
                "chapter_href": chapter_href,
                "chapter_progression": float(chapter_progression),
                "epub_sha256": local_hash,
            })
    return {
        "id": "storyteller:{}".format(book_path_id),
        "label": "Storyteller",
        "kind": "storyteller",
        "observation": "last_reported",
        "progress_percent": percentage,
        "chapter_progress_percent": (
            float((locator.get("locations") or {}).get("progression")) * 100.0
            if isinstance((locator.get("locations") or {}).get("progression"), (int, float))
            else None
        ),
        "observed_at": observed_at,
        "locator_type": "readium_locator",
        "edition": {"match": "sha256" if same_edition else "different"},
        "resume": resume,
        "writeback": "read_only",
    }
