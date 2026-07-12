# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Orchestration layer for the cover-picker page.

Runs all enabled metadata providers, post-processes their results
through ``cover_booster``, optionally adds the embedded-cover-from-file
candidate, and returns a flattened list of cover-only candidates ready
for the picker grid. The picker template + blueprint are thin views over
this; all the orchestration logic lives here so it's testable without a
Flask app.

Architecture intent: any new metadata provider added to
``cps/metadata_provider/`` automatically contributes candidates here.
The picker has zero per-source registration code — match the existing
provider auto-discovery pattern. Same providers, same toggles, same API
keys; only the surface they're presented through differs.
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import os
import time
from dataclasses import asdict
from typing import Callable, Dict, Iterable, List, Optional

from .. import logger
from . import cover_booster
from .cover_booster import boost_covers


log = logger.create()


_DEFAULT_TIMEOUT = float(os.environ.get("CWA_COVER_PICKER_TIMEOUT", "12"))
_DEFAULT_WORKERS = int(os.environ.get("CWA_COVER_PICKER_WORKERS", "5"))
_DEFAULT_MAX_PER_SOURCE = int(os.environ.get("CWA_COVER_PICKER_MAX_CANDIDATES", "30"))


@dataclasses.dataclass
class CoverCandidate:
    """One cover the picker grid can show. Lighter than ``MetaRecord``
    because we don't need full metadata — just enough to identify the
    source and let the user choose."""

    source_id: str          # 'hardcover', 'openlibrary', 'embedded', 'url', 'upload'
    source_name: str        # Display label for the source badge
    cover_url: str          # http(s) URL or data: URL
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    publisher: Optional[str] = None
    year: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    candidate_id: Optional[str] = None      # Stable id for the apply step
    flags: Optional[List[str]] = None       # 'low_res', 'squished', etc.

    def to_dict(self) -> dict:
        return asdict(self)


@dataclasses.dataclass
class ProviderStatus:
    """Per-provider status surfaced to the UI — same shape as
    ``search_metadata.metadata_search`` returns so the picker page can
    render the same status row."""

    id: str
    name: str
    status: str          # ok | empty | error | disabled | missing_key | rate_limited | blocked
    count: int
    message: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def gather_cover_candidates(
    *,
    providers: Iterable,
    query: str,
    static_cover: str,
    locale: str,
    is_provider_enabled: Callable[[object], bool] = lambda _p: True,
    classify_failure: Callable[[Exception], tuple] = lambda exc: ("error", str(exc) or exc.__class__.__name__),
    classify_empty: Callable[[object], tuple] = lambda _p: ("empty", "No results"),
    extract_embedded: Optional[Callable[[], "ExtractedCover | None"]] = None,
    book_isbns: Iterable[str] = (),
) -> tuple[List[CoverCandidate], List[ProviderStatus]]:
    """Run every enabled provider against ``query`` in parallel, post-process
    through ``cover_booster``, and return a flattened candidate list +
    per-provider status.

    Caller responsibilities (kept out of this module so it stays
    Flask-free):

      * ``providers`` — iterable of provider instances. Match
        ``cps.search_metadata.cl``'s shape.
      * ``is_provider_enabled(provider)`` — gate function; the picker can
        respect both per-user and global enablement here.
      * ``classify_failure`` / ``classify_empty`` — share the same
        classifiers ``search_metadata.metadata_search`` uses so the UI
        copy stays consistent.
      * ``extract_embedded`` — zero-arg callable that returns an
        ``ExtractedCover`` or None. Lets us inject the embedded candidate
        without coupling this module to ``cover_extract``.
      * ``book_isbns`` — the editing book's stored ISBN identifiers. These
        can contribute an Amazon-CDN high-resolution cover independently of
        whether the Amazon metadata provider itself is enabled.
    """
    runnable, statuses = _filter_runnable(providers, is_provider_enabled)
    candidates: List[CoverCandidate] = []

    if extract_embedded is not None:
        try:
            embedded = extract_embedded()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("embedded cover extract failed: %s", exc)
            embedded = None
        if embedded is not None:
            data_url = _bytes_to_data_url(embedded.data, embedded.mime_type)
            candidates.append(CoverCandidate(
                source_id="embedded",
                source_name="Current embedded cover",
                cover_url=data_url,
                candidate_id="embedded",
            ))

    amazon_isbn10s = _amazon_candidate_isbn10s(book_isbns)
    if (not runnable or not query) and not amazon_isbn10s:
        return candidates, statuses

    started_at = {p.__id__: time.monotonic() for p in runnable}
    results_by_provider: Dict[str, list] = {}
    amazon_urls_by_isbn10: Dict[str, str] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=_DEFAULT_WORKERS) as pool:
        futures = {}
        if query:
            futures.update({
                pool.submit(p.search, query, static_cover, locale): ("provider", p)
                for p in runnable
            })
        futures.update({
            pool.submit(cover_booster._amazon_cdn_cover_for_isbn10, isbn10): ("amazon", isbn10)
            for isbn10 in amazon_isbn10s
        })
        for fut in concurrent.futures.as_completed(futures):
            kind, subject = futures[fut]
            if kind == "amazon":
                try:
                    url = fut.result()
                except Exception as exc:  # pragma: no cover - defensive
                    log.debug("cover-picker Amazon CDN probe failed for %s: %s", subject, exc)
                    continue
                if url:
                    amazon_urls_by_isbn10[subject] = url
                continue

            p = subject
            elapsed_ms = int((time.monotonic() - started_at.get(p.__id__, time.monotonic())) * 1000)
            try:
                hits = fut.result() or []
            except Exception as exc:
                status, message = classify_failure(exc)
                log.warning("cover-picker provider %s failed (%s) in %dms: %s",
                            p.__class__.__name__, status, elapsed_ms, exc)
                statuses.append(ProviderStatus(
                    id=p.__id__, name=p.__name__, status=status,
                    count=0, message=message, duration_ms=elapsed_ms,
                ))
                continue
            results_by_provider[p.__id__] = hits[:_DEFAULT_MAX_PER_SOURCE]
            count = len(results_by_provider[p.__id__])
            status, message = ("ok", "") if count else classify_empty(p)
            statuses.append(ProviderStatus(
                id=p.__id__, name=p.__name__, status=status,
                count=count, message=message, duration_ms=elapsed_ms,
            ))

    flat = []
    for hits in results_by_provider.values():
        flat.extend([asdict(h) for h in hits if h])
    try:
        boost_covers(flat)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("cover-picker boost pass failed: %s", exc)

    for record in flat:
        cover_url = record.get("cover") or ""
        if not cover_url or _is_generic_cover(cover_url, static_cover):
            continue
        source = record.get("source") or {}
        candidates.append(CoverCandidate(
            source_id=source.get("id") or "unknown",
            source_name=source.get("description") or "Unknown",
            cover_url=cover_url,
            title=record.get("title"),
            authors=record.get("authors") or [],
            publisher=record.get("publisher") or None,
            year=_year_from_published_date(record.get("publishedDate")),
            candidate_id=f"{source.get('id') or 'src'}:{record.get('id') or ''}",
        ))

    existing_urls = {candidate.cover_url for candidate in candidates}
    for isbn10 in amazon_isbn10s:
        cover_url = amazon_urls_by_isbn10.get(isbn10)
        if not cover_url or cover_url in existing_urls:
            continue
        candidates.append(CoverCandidate(
            source_id="amazon_highres",
            source_name="Amazon (high-res)",
            cover_url=cover_url,
            candidate_id=f"amazon_highres:{isbn10}",
        ))
        existing_urls.add(cover_url)

    statuses.sort(key=lambda s: s.name.lower())
    return candidates, statuses


def _amazon_candidate_isbn10s(book_isbns: Iterable[str]) -> List[str]:
    """Normalize stored book ISBNs for the existing Amazon-CDN probe.

    The booster's flag is deliberately the single kill-switch for both its
    provider-record upgrade and this independent picker candidate.
    """
    if not cover_booster._AMAZON_CDN_ENABLED:
        return []
    isbn10s: List[str] = []
    for isbn in book_isbns or ():
        isbn10 = cover_booster._to_isbn10(str(isbn or ""))
        if isbn10 and isbn10 not in isbn10s:
            isbn10s.append(isbn10)
    return isbn10s


def _filter_runnable(providers, is_provider_enabled) -> tuple[list, List[ProviderStatus]]:
    runnable: list = []
    statuses: List[ProviderStatus] = []
    for p in providers:
        if not is_provider_enabled(p):
            statuses.append(ProviderStatus(
                id=p.__id__, name=p.__name__, status="disabled", count=0,
                message="Disabled in settings", duration_ms=0,
            ))
            continue
        runnable.append(p)
    return runnable, statuses


def _is_generic_cover(cover_url: str, static_cover: str) -> bool:
    if not cover_url:
        return True
    if static_cover and cover_url.endswith(static_cover.split("/")[-1]):
        return True
    if cover_url.endswith("generic_cover.svg"):
        return True
    return False


def _year_from_published_date(value) -> Optional[str]:
    if not value:
        return None
    s = str(value)
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else None


def _bytes_to_data_url(data: bytes, mime_type: str) -> str:
    """Serialize raw image bytes to a data URL the picker grid can render
    without a separate fetch. Used only for the embedded-cover candidate;
    typical EPUB cover is 50-300 KB which is fine inline."""
    import base64
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
