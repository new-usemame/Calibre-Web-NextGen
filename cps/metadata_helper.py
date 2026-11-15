# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import json
import re
import copy
import unicodedata
from difflib import SequenceMatcher

from cps import logger, db
from cps.metadata_constants import (
    DEFAULT_METADATA_PROVIDER_HIERARCHY,
    DEFAULT_METADATA_PROVIDER_HIERARCHY_JSON,
)
from cps.search_metadata import cl as metadata_providers
from cps.cwa_db_loader import load_cwa_db
CWA_DB = load_cwa_db().CWA_DB

log = logger.create()

def fetch_and_apply_metadata(book_id: int, user_enabled: bool = False) -> bool:
    """
    Fetch metadata for a newly ingested book and apply it if settings allow.
    
    Args:
        book_id: The ID of the book to fetch metadata for
        user_enabled: Deprecated parameter - metadata fetching is now admin-controlled only
        
    Returns:
        bool: True if metadata was successfully fetched and applied, False otherwise
    """
    try:
        if not db.CalibreDB.session_factory:
            log.error("CalibreDB not initialized; skipping metadata fetch")
            return False

        # Check global settings (admin-controlled only)
        cwa_db = CWA_DB()
        cwa_settings = cwa_db.get_cwa_settings()
        
        if not cwa_settings.get('auto_metadata_fetch_enabled', False):
            log.debug("Auto metadata fetch disabled by administrator")
            return False
            
        # Get the book
        calibre_db_instance = db.CalibreDB(expire_on_commit=False, init=True)
        book = calibre_db_instance.get_book(book_id)
        if not book:
            log.error(f"Book with ID {book_id} not found")
            return False
            
        # Create search query from book title and author
        search_query = book.title
        if book.authors:
            author_names = [author.name for author in book.authors]
            search_query += " " + " ".join(author_names)

        # The book's existing ISBN is used to pick the matching edition from the
        # provider's results instead of blindly taking the first one (fork #402).
        book_isbn = _book_isbn(book)

        log.info(f"Fetching metadata for: {search_query}")

        # Get provider hierarchy (single source of truth — fork #405)
        try:
            provider_hierarchy = json.loads(cwa_settings.get('metadata_provider_hierarchy', DEFAULT_METADATA_PROVIDER_HIERARCHY_JSON))
            if not isinstance(provider_hierarchy, list):
                raise ValueError("metadata_provider_hierarchy must be a list")
        except (json.JSONDecodeError, TypeError, ValueError):
            provider_hierarchy = list(DEFAULT_METADATA_PROVIDER_HIERARCHY)

        # Global provider enablement map
        enabled_map = _parse_metadata_providers_enabled(
            cwa_settings.get('metadata_providers_enabled', '{}')
        )
            
        # Try each provider in order
        metadata_found = False
        series_fill = _NOT_ASKED
        for provider_id in provider_hierarchy:
            # Check if explicitly disabled (default is enabled if not specified)
            from cps.metadata_constants import metadata_provider_enabled
            is_enabled = metadata_provider_enabled(provider_id, enabled_map)
            if not is_enabled:
                log.debug(f"Provider {provider_id} is globally disabled")
                continue
            try:
                # Find the provider
                provider = None
                for p in metadata_providers:
                    if p.__id__ == provider_id:
                        provider = p
                        break
                        
                if not provider or not provider.active:
                    continue
                    
                log.debug(f"Trying metadata provider: {provider.__name__}")
                
                # Search for metadata
                results = provider.search(search_query, "", "en")
                if not results or len(results) == 0:
                    continue
                    
                # Prefer the candidate whose ISBN matches the book's existing ISBN
                # over a blind first result (fork #402), and refuse a candidate
                # that isn't plausibly this book at all (fork #1164).
                metadata = _select_metadata_result(
                    results, book_isbn,
                    book_title=book.title, book_authors=book.authors,
                )
                if metadata is None:
                    log.info(
                        f"No confident match from {provider.__name__} for book: "
                        f"{book.title} — leaving its metadata untouched"
                    )
                    continue

                # Captured before the apply: _apply_metadata_to_book overwrites
                # book.title, so logging it afterwards named the book we fetched
                # rather than the book we changed (fork #1164).
                searched_title = book.title

                # A matched record without a series takes one from a provider
                # that carries series, applied with the rest of the record.
                if not (getattr(metadata, "series", None) or "").strip():
                    if series_fill is _NOT_ASKED:
                        series_fill = _find_missing_series(book, search_query, provider_id,
                                                           provider_hierarchy, enabled_map)
                    if series_fill:
                        metadata = copy.copy(metadata)
                        metadata.series, metadata.series_index = series_fill

                # Apply metadata to book
                if _apply_metadata_to_book(book, metadata, calibre_db_instance):
                    log.info(f"Successfully applied metadata from {provider.__name__} for book: {searched_title}")
                    metadata_found = True
                    break
                    
            except Exception as e:
                log.warning(f"Error fetching metadata from provider {provider_id}: {e}")
                # Each provider must start on a usable session: one that failed
                # mid-flush would otherwise fail every provider after it (#2435).
                calibre_db_instance.session.rollback()
                continue
                
        calibre_db_instance.session.close()
        return metadata_found
        
    except Exception as e:
        log.error(f"Error in fetch_and_apply_metadata: {e}", exc_info=True)
        return False


def _apply_metadata_to_book(book, metadata, calibre_db_instance) -> bool:
    """
    Apply fetched metadata to a book record.
    
    Args:
        book: The book database record
        metadata: The metadata record from provider
        calibre_db_instance: Database instance
        
    Returns:
        bool: True if metadata was successfully applied
    """
    cover_stage = None
    # Read before any flush can fail: afterwards the session refuses to load
    # expired attributes until it is rolled back.
    book_id = getattr(book, 'id', 'unknown')
    previous_cover_metadata = (
        getattr(book, 'has_cover', 0),
        getattr(book, 'last_modified', None),
    )
    updated_without_cover = False
    try:
        # Get CWA settings to check smart application preference and field selections
        cwa_db = CWA_DB()
        cwa_settings = cwa_db.get_cwa_settings()
        use_smart_application = cwa_settings.get('auto_metadata_smart_application', False)
        
        updated = False
        
        # Update title - only if enabled in settings. Smart mode only fills a
        # missing title. It used to take the longer one, and a summary or study
        # guide's title is nearly always longer than the book's.
        if (cwa_settings.get('auto_metadata_update_title', True) and 
            metadata.title and metadata.title.strip()):
            if use_smart_application:
                if not _has_meaningful_title(book):
                    book.title = metadata.title.strip()
                    updated = True
            else:
                book.title = metadata.title.strip()
                updated = True
            
        # Update authors - only if enabled in settings. In smart mode, never clear
        # an existing meaningful author (fork #403): a weak match must not replace a
        # correct author with a foreign edition's translator.
        if (cwa_settings.get('auto_metadata_update_authors', True) and
            metadata.authors and len(metadata.authors) > 0 and
            not (use_smart_application and _has_meaningful_authors(book))):
            # Clear existing authors
            book.authors.clear()
            for author_name in metadata.authors:
                if author_name and author_name.strip():
                    author = calibre_db_instance.get_author_by_name(author_name.strip())
                    if not author:
                        author = db.Authors(author_name.strip(), author_name.strip())
                        calibre_db_instance.session.add(author)
                    book.authors.append(author)
            updated = True
            
        # Update description - only if enabled in settings
        if (cwa_settings.get('auto_metadata_update_description', True) and 
            metadata.description and metadata.description.strip()):
            current_description = book.comments[0].text if book.comments else ""
            # Smart mode only fills a missing description, for the same reason as
            # the title: the longer text is often a summary's disclaimer.
            if use_smart_application:
                if not _has_meaningful_description(current_description):
                    if book.comments:
                        book.comments[0].text = metadata.description.strip()
                    else:
                        comment = db.Comments(metadata.description.strip(), book.id)
                        calibre_db_instance.session.add(comment)
                    updated = True
            else:
                if book.comments:
                    book.comments[0].text = metadata.description.strip()
                else:
                    comment = db.Comments(metadata.description.strip(), book.id)
                    calibre_db_instance.session.add(comment)
                updated = True
            
        # Update publisher - only if enabled in settings
        if (cwa_settings.get('auto_metadata_update_publisher', True) and 
            metadata.publisher and metadata.publisher.strip()):
            if use_smart_application:
                if not book.publishers or len(book.publishers) == 0:
                    publisher = calibre_db_instance.get_publisher_by_name(metadata.publisher.strip())
                    if not publisher:
                        publisher = db.Publishers(metadata.publisher.strip(), metadata.publisher.strip())
                        calibre_db_instance.session.add(publisher)
                    book.publishers = [publisher]
                    updated = True
            else:
                # Clear existing publishers and add new one
                book.publishers.clear()
                publisher = calibre_db_instance.get_publisher_by_name(metadata.publisher.strip())
                if not publisher:
                    publisher = db.Publishers(metadata.publisher.strip(), metadata.publisher.strip())
                    calibre_db_instance.session.add(publisher)
                book.publishers = [publisher]
                updated = True
                
        # Update tags if available and enabled in settings
        if (cwa_settings.get('auto_metadata_update_tags', True) and 
            hasattr(metadata, 'tags') and metadata.tags):
            for tag_name in metadata.tags:
                if tag_name and tag_name.strip():
                    tag = calibre_db_instance.get_tag_by_name(tag_name.strip())
                    if not tag:
                        tag = db.Tags(name=tag_name.strip())
                        calibre_db_instance.session.add(tag)
                    if tag not in book.tags:
                        book.tags.append(tag)
                        # Only a tag the book lacked is a change: a recorded
                        # change rewrites the file and re-sends it to a Kobo.
                        updated = True
            
        # Update series if available and enabled in settings. Smart mode keeps an
        # existing series rather than overwriting it (fork #403).
        if (cwa_settings.get('auto_metadata_update_series', True) and
            hasattr(metadata, 'series') and metadata.series and metadata.series.strip() and
            not (use_smart_application and book.series)):
            series = (calibre_db_instance.get_series_by_name(metadata.series.strip())
                      or _library_series_like(calibre_db_instance, metadata.series.strip(), book))
            if not series:
                series = db.Series(metadata.series.strip(), metadata.series.strip())
                calibre_db_instance.session.add(series)
            book.series.clear()
            book.series.append(series)
            
            # Set series index if available
            if hasattr(metadata, 'series_index') and metadata.series_index:
                try:
                    # Convert to float first to validate, then store as string (DB column is String)
                    float_value = float(metadata.series_index)
                    book.series_index = str(float_value)
                except (ValueError, TypeError):
                    book.series_index = '1.0'
            updated = True
            
        # Update published date if available and enabled in settings. Smart mode
        # keeps an existing publication date (fork #403).
        if (cwa_settings.get('auto_metadata_update_published_date', True) and
            hasattr(metadata, 'publishedDate') and metadata.publishedDate and
            not (use_smart_application and _has_pubdate(book))):
            try:
                from datetime import datetime
                if isinstance(metadata.publishedDate, str):
                    # Try to parse various date formats
                    for fmt in ['%Y-%m-%d', '%Y-%m', '%Y']:
                        try:
                            book.pubdate = datetime.strptime(metadata.publishedDate, fmt).date()
                            updated = True
                            break
                        except ValueError:
                            continue
                elif hasattr(metadata.publishedDate, 'date'):
                    book.pubdate = metadata.publishedDate.date()
                    updated = True
            except Exception as e:
                log.warning(f"Error parsing published date: {e}")
                
        # Update rating if available and enabled in settings. Smart mode keeps an
        # existing rating (fork #403).
        if (cwa_settings.get('auto_metadata_update_rating', True) and
            hasattr(metadata, 'rating') and metadata.rating and
            not (use_smart_application and book.ratings)):
            try:
                rating_value = float(metadata.rating)
                # MetaRecord.rating is 0-5 stars; calibre stores half-stars, 0-10.
                if 0 < rating_value <= 5:
                    # ratings is a shared lookup table (one row per value), so link
                    # the book to the row for its value rather than inserting a
                    # duplicate or rewriting a row other books point at (#2435).
                    book.ratings = [_rating_row(calibre_db_instance.session, int(rating_value * 2))]
                    updated = True
            except (ValueError, TypeError):
                pass
                
        # Update identifiers if available and enabled in settings. Smart mode fills
        # in identifier types the book is missing but never overwrites an existing
        # value — so a correct ISBN is not clobbered by a foreign edition's (fork #403).
        if (cwa_settings.get('auto_metadata_update_identifiers', True) and
            hasattr(metadata, 'identifiers') and metadata.identifiers):
            for identifier_type, identifier_value in metadata.identifiers.items():
                if identifier_type and identifier_value:
                    # Check if identifier already exists
                    existing = False
                    for identifier in book.identifiers:
                        if identifier.type == identifier_type:
                            if not use_smart_application:
                                identifier.val = identifier_value
                                updated = True
                            existing = True
                            break
                    if not existing:
                        new_identifier = db.Identifiers(identifier_value, identifier_type, book.id)
                        calibre_db_instance.session.add(new_identifier)
                        book.identifiers.append(new_identifier)
                        updated = True
        
        # Text fields changed above. Recorded before the cover step, which also
        # sets ``updated``, so the enforcement record below can tell them apart.
        text_updated = updated

        # Cover image (fork #404): download and apply through the same
        # validated path the manual editor uses. Smart mode only fills a
        # missing cover; the per-book cover lock is honored in both modes.
        cover_updated = False
        if (cwa_settings.get('auto_metadata_update_cover', True) and
                not (use_smart_application and getattr(book, 'has_cover', 0))):
            cover_stage = _apply_cover_from_metadata(book, metadata)
            if cover_stage:
                updated_without_cover = updated
                updated = True
                cover_updated = True
                book.has_cover = 1

        if updated:
            # Any applied field is a book change and has to be recorded as one,
            # so Kobo sync (``last_modified > sync_token``) picks up a fetched
            # description or publisher too, not only a cover.
            #
            # A replaced cover IS a book change and has to be recorded as one.
            # Writing cover.jpg alone leaves Books.last_modified untouched, and
            # that column is the version token every cover URL carries — so an
            # automated metadata fetch used to swap the image while every UI kept
            # asking for the old URL. Cover responses are cacheable now, which
            # turns that from "stale until the page reloads" into "stale until
            # the browser evicts it", so this bump is load-bearing rather than
            # cosmetic. Single source of truth: helper.mark_book_modified.
            #
            # set_dirty=False on purpose: `book` belongs to calibre_db_instance's
            # session, while helper's set_metadata_dirty writes through helper's
            # own module-level calibre_db — a different session, and the wrong
            # one to touch from the ingest subprocess. The caller owns the
            # metadata write-back for this very fetch; the timestamp is the part
            # that was being dropped, and it is a plain attribute assignment.
            from cps import helper
            helper.mark_book_modified(book, set_dirty=False)

        if updated:
            calibre_db_instance.session.commit()
            if cover_updated:
                from cps import helper
                published, publish_error = cover_stage.publish()
                if not published:
                    cover_stage.discard()
                    cover_stage = None
                    if text_updated:
                        # The text fields did change, so keep their timestamp.
                        book.has_cover = previous_cover_metadata[0]
                    else:
                        book.has_cover, book.last_modified = previous_cover_metadata
                    try:
                        calibre_db_instance.session.commit()
                    except Exception as compensation_error:
                        log.error(
                            f"Cover metadata compensation failed for book {book.id} "
                            f"after publish failure {publish_error}: {compensation_error}"
                        )
                        calibre_db_instance.session.rollback()
                    if text_updated:
                        _queue_metadata_enforcement(book, {'metadata': True})
                    return updated_without_cover
                cover_stage = None
                # #707: the new cover must also be embedded into the book file,
                # not only written to cover.jpg. Best-effort by design — it
                # queues an enforcement record, it does not perform the embed.
                # AFTER the commit: the enforcer reads the book back out of
                # metadata.db, so a record published ahead of the transaction
                # can be consumed against the pre-commit row.
                changed = {'cover': True}
                if text_updated:
                    changed['metadata'] = True
                _queue_metadata_enforcement(book, changed)
                # Regenerate the cached thumbnails so the new cover shows in
                # the grid, same as a manual cover edit.
                try:
                    helper.replace_cover_thumbnail_cache(
                        book.id, book_path=book.path,
                        last_modified=getattr(book, 'last_modified', None))
                except Exception as e:
                    log.warning(f"Cover thumbnail refresh failed for book {book.id}: {e}")
            elif text_updated:
                # Text-only fetch: queue the same file embed a manual edit gets,
                # after the commit for the same reason as the cover above.
                _queue_metadata_enforcement(book, {'metadata': True})

        return updated
        
    except Exception as e:
        calibre_db_instance.session.rollback()
        log.error(f"Error applying metadata to book {book_id}: {e}")
        if cover_stage is not None:
            cover_stage.discard()
        book.has_cover, book.last_modified = previous_cover_metadata
        return False


def _queue_metadata_enforcement(book, changed):
    """Queue the file-level embed for ``book`` (#707). Best-effort, never raises."""
    try:
        from cps import helper
        helper.log_metadata_change(book, changed)
    except Exception as e:
        log.warning(f"Metadata enforcement record failed for book {getattr(book, 'id', '?')}: {e}")


def _rating_row(session, value):
    """Return the shared ``ratings`` row for ``value``, creating it if the library has none."""
    rating = session.query(db.Ratings).filter(db.Ratings.rating == value).first()
    if rating is None:
        rating = db.Ratings(rating=value)
        session.add(rating)
    return rating


def _apply_cover_from_metadata(book, metadata):
    """Download and validate a staged provider cover for ``book`` (fork #404).

    Routed through ``helper.save_cover_from_url`` so the ingest auto-fetch
    gets the exact safeguards of the manual editor: advocate SSRF guard,
    download size cap, and image-format validation. Returns a staged-cover
    handle; the caller publishes it only after the metadata commit.

    Fully self-contained failure boundary: this runs in the ingest
    processor (no Flask app context, where the error-path ``_()`` calls in
    helper can raise) and a cover problem must never void the rest of the
    metadata application.
    """
    cover_url = str(getattr(metadata, 'cover', '') or '').strip()
    if not cover_url or cover_url.endswith('/static/generic_cover.svg'):
        # Providers fall back to the placeholder image when an edition has
        # no cover — never overwrite a real cover with the placeholder.
        return False
    try:
        from cps import helper
        if helper.book_cover_is_locked(book.id):
            log.info(f"Auto metadata fetch: cover locked for book {book.id}, skipping")
            return False
        result, error = helper.save_cover_from_url(cover_url, book.path)
        if result:
            return result
        log.warning(f"Auto metadata fetch: cover download failed for book {book.id}: {error}")
    except Exception as e:
        log.warning(f"Auto metadata fetch: cover apply failed for book {book.id}: {e}")
    return False


def _normalize_isbn(value):
    """Strip separators and upper-case so ISBNs compare regardless of formatting."""
    return "".join(ch for ch in str(value) if ch.isalnum()).upper()


# Trailing words that name the kind of series rather than the series itself:
# "Dune Chronicles" is the library's "Dune", "Red Rising Saga" its "Red Rising".
_SERIES_KIND_WORDS = frozenset({
    "series", "saga", "trilogy", "chronicles", "cycle", "sequence", "novels",
    "duology", "dilogy", "dylogy", "quartet", "quintet",
})

# How many series-capable providers the fill step may ask per book.
_SERIES_FILL_LIMIT = 3

# Series-capable providers the fill asks even when the configured order leaves
# them out: both carry series data of their own. Any other provider (Amazon,
# Goodreads and the rest scrape pages that throttle bots) is asked only when
# the administrator put it in the order.
_SERIES_FILL_EXTRAS = ("hardcover", "kobo")

# The fill has not run yet for this book (None means it ran and found nothing).
_NOT_ASKED = object()

# A trailing note in brackets: "(Publication Order)", "[Legends]".
_SERIES_NOTE = re.compile(r"\s*[(\[][^()\[\]]*[)\]]\s*$")


def _series_key(name):
    """A series name folded for comparison: case, Latin accents, punctuation, a
    leading "The", trailing kind words (``_SERIES_KIND_WORDS``) and a trailing
    note in brackets ("(Publication Order)") ignored. Letters of every script
    are kept, so two Japanese titles never fold to the same bare number."""
    text = _SERIES_NOTE.sub("", name or "")
    folded = []
    for ch in unicodedata.normalize("NFC", text):
        base = unicodedata.normalize("NFD", ch)[0]
        folded.append(base if base.isascii() else ch)
    words = re.findall(r"[^\W_]+", "".join(folded).casefold())
    if words[:1] == ["the"] and len(words) > 1:
        words = words[1:]
    while len(words) > 1 and words[-1] in _SERIES_KIND_WORDS:
        words = words[:-1]
    return " ".join(words)


def _series_note(name):
    """The trailing bracketed note of a series name, folded, or ""."""
    found = _SERIES_NOTE.search(name or "")
    return found.group(0).strip(" ()[]").casefold() if found else ""


def _library_series_like(calibre_db_instance, name, book=None):
    """The library's existing series that ``name`` is another spelling of, or None.

    Sources disagree on what to call a series (Open Library's "Dune Chronicles",
    Hardcover's "The Chronicles of Narnia (Publication Order)"), and every new
    spelling became a second series beside the first. Only an exact key match counts, and only when it is unique.
    """
    wanted = _series_key(name)
    if not wanted:
        return None
    try:
        rows = calibre_db_instance.session.query(db.Series).all()
    except Exception:
        return None
    note = _series_note(name)

    def same_series(row):
        # Two different notes name two different series: "Star Wars (Legends)"
        # is not "Star Wars (Canon)", and a publication-order position is
        # wrong inside a chronological-order series.
        row_name = getattr(row, "name", "")
        row_note = _series_note(row_name)
        return (_series_key(row_name) == wanted
                and not (note and row_note and note != row_note)
                and _shares_an_author(row, book))

    matches = [row for row in rows if same_series(row)]
    return matches[0] if len(matches) == 1 else None


def _shares_an_author(series_row, book):
    """False when the library series holds books and none is by one of the
    book's authors: a stem shared across authors ("Inheritance Cycle",
    "Inheritance Trilogy") is two series, not two spellings of one."""
    if book is None:
        return True
    shelved = list(getattr(series_row, "books", None) or [])
    if not shelved:
        return True
    wanted = {getattr(a, "name", "").casefold() for a in getattr(book, "authors", None) or []}
    wanted.discard("")
    return any(getattr(a, "name", "").casefold() in wanted
               for other in shelved for a in (getattr(other, "authors", None) or []))


def _find_missing_series(book, search_query, applied_provider_id, provider_hierarchy,
                         enabled_map):
    """Ask series-capable providers for the series a matched record left empty.

    Auto-fetch applies the first provider whose result passes the match gate
    and stops. When that provider carries no series (Google Books, Open Library
    and IBDb never do), a provider further down that has one was never asked.
    This asks the providers marked ``FILLS_SERIES``: those in the configured
    order first, then ``_SERIES_FILL_EXTRAS`` if the order leaves them out, at
    most ``_SERIES_FILL_LIMIT`` of them, and returns ``(series, position)``
    from the first result that passes the same gate, or None.

    The caller folds the answer into the record it is about to apply, so the
    book gets one commit and one file-embed record. Never raises: the caller
    is inside its provider loop, and an exception there would send the loop on
    to the next provider's whole record.
    """
    try:
        return _find_missing_series_unguarded(book, search_query, applied_provider_id,
                                              provider_hierarchy, enabled_map)
    except Exception as e:
        log.warning(f"Series fill failed for book {getattr(book, 'id', 'unknown')}: {e}")
        return None


def _find_missing_series_unguarded(book, search_query, applied_provider_id, provider_hierarchy,
                                   enabled_map):
    try:
        cwa_settings = CWA_DB().get_cwa_settings()
    except Exception:
        return None
    if not cwa_settings.get('auto_metadata_update_series', True) or getattr(book, "series", None):
        return None
    from cps.metadata_constants import metadata_provider_enabled
    by_id = {p.__id__: p for p in metadata_providers}
    ordered = [pid for pid in provider_hierarchy if pid in by_id]
    ordered += [pid for pid in _SERIES_FILL_EXTRAS if pid in by_id and pid not in ordered]
    asked = 0
    for provider_id in ordered:
        provider = by_id[provider_id]
        if (provider_id == applied_provider_id or not getattr(provider, "FILLS_SERIES", False)
                or not provider.active or not metadata_provider_enabled(provider_id, enabled_map)):
            continue
        if asked >= _SERIES_FILL_LIMIT:
            break
        asked += 1
        try:
            results = provider.search(search_query, "", "en") or []
        except Exception as e:
            log.info(f"Series fill: {provider.__name__} failed for book {book.id}: {e}")
            continue
        chosen = _select_metadata_result(results, _book_isbn(book),
                                         book_title=book.title, book_authors=book.authors) if results else None
        series = (getattr(chosen, "series", None) or "") if chosen is not None else ""
        if not isinstance(series, str) or not series.strip():
            continue
        position = getattr(chosen, "series_index", None)
        log.info(f"Series fill: {series.strip()} #{position or ''} from {provider.__name__} for book {book.id}")
        return series.strip(), position
    return None


def _book_isbn(book):
    """Return the book's existing ISBN (preferring ISBN-13) or None (fork #402)."""
    isbn13 = isbn = None
    for ident in (getattr(book, "identifiers", None) or []):
        itype = (getattr(ident, "type", "") or "").lower()
        if itype in ("isbn13", "isbn_13"):
            isbn13 = ident.val
        elif itype == "isbn":
            isbn = ident.val
    return isbn13 or isbn


# How close two titles must be, when their identifying words are NOT identical,
# before metadata is applied. Deliberately near-exact: this path exists only to
# absorb spelling/diacritic noise, not to judge whether two different titles are
# "close enough". Applying nothing is recoverable; silently overwriting a book
# with a different book's metadata is not (fork #1164).
#
# A plain character-similarity bar cannot do this job on its own: series titles
# share a long prefix, so "Harry Potter and the Chamber of Secrets" scores 0.78
# against "...and the Goblet of Fire" — high enough to pass any threshold loose
# enough to accept real-world title variance. Identifying-word equality is what
# separates "same book, different edition" from "next book in the series".
_TITLE_FUZZY_MATCH_MIN = 0.92

# Joining words carry no identifying weight and inflate similarity between
# unrelated titles in the same series.
_TITLE_STOPWORDS = frozenset((
    "and", "or", "the", "a", "an", "of", "in", "on", "to", "for", "with",
    "from", "at", "by", "its", "his", "her", "their",
))

# Generic descriptors publishers append to a title. Stripped so "The Devils"
# still matches "The Devils: A Novel". Deliberately a short curated list of
# non-identifying words — a substantive subtitle ("Dune: Messiah") must NOT be
# stripped, or every book in a series collapses onto its sibling.
_GENERIC_TITLE_TAILS = (
    "a novel", "an novel", "novel", "a memoir", "a story", "stories",
    "a biography", "an autobiography", "unabridged", "abridged",
)

_LEADING_ARTICLES = ("the ", "a ", "an ")


def _normalize_title(value):
    """Casefold and strip accents, bracketed asides, punctuation, a leading
    article and any generic publisher tail, so titles compare on the words that
    actually identify the book (fork #1164)."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    # Series markers and format notes: "(First Law World)", "[Unabridged]".
    text = re.sub(r"[(\[{][^)\]}]*[)\]}]", " ", text)
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    text = " ".join(text.split())
    for article in _LEADING_ARTICLES:
        if text.startswith(article):
            text = text[len(article):]
            break
    for tail in _GENERIC_TITLE_TAILS:
        if text.endswith(" " + tail):
            stripped = text[: -(len(tail) + 1)].strip()
            if stripped:
                text = stripped
                break
    return text


# Words that announce a volume number, with the spelling each one compares as,
# so "Vol. 2", "Volume 2" and "Vol 2" are the same words (fork #2479). A roman
# numeral or number word only counts as a number straight after one of these
# ("Part II", "Book Two"), because "I" and "one" are ordinary words elsewhere.
_VOLUME_MARKERS = {
    "vol": "vol", "vols": "vol", "volume": "vol", "part": "part", "pt": "part",
    "book": "book", "bk": "book", "no": "no", "number": "no", "issue": "issue",
    "episode": "episode", "season": "season", "chapter": "chapter",
    "tome": "tome",
}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

_ROMAN_NUMERALS = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
    "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
    "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20,
}

# A marker written straight onto its number: "Vol12", "Pt3".
_GLUED_VOLUME_RE = re.compile(r"^(" + "|".join(
    sorted(_VOLUME_MARKERS, key=len, reverse=True)) + r")(\d+)$")


def _title_tokens(value):
    """Normalized title words with volume markers and the numbers after them
    in one spelling: "Volume Two" and "Vol. 2" both become ``vol 2``
    (fork #2479)."""
    tokens = []
    for word in _normalize_title(value).split():
        glued = _GLUED_VOLUME_RE.match(word)
        if glued:
            tokens.extend((_VOLUME_MARKERS[glued.group(1)], str(int(glued.group(2)))))
            continue
        if word.isdecimal():
            tokens.append(str(int(word)))
            continue
        # "no" is too common a word ("No One Lives Forever") to turn the word
        # after it into a number; "No. 5" is still read through its digits.
        if tokens and tokens[-1] in _VOLUME_MARKERS.values() and tokens[-1] != "no":
            number = _NUMBER_WORDS.get(word) or _ROMAN_NUMERALS.get(word)
            if number:
                tokens.append(str(number))
                continue
        tokens.append(_VOLUME_MARKERS.get(word, word))
    return tokens


def _title_content_words(value):
    """The identifying words of a title, in order, with joining words removed
    (fork #1164). Order is kept so "Blood and Iron" doesn't match "Iron and
    Blood". A volume marker right before its number is dropped, so "One
    Piece, Vol. 12" and "ONE PIECE 12" are the same words (fork #2479)."""
    tokens = _title_tokens(value)
    return tuple(
        w for i, w in enumerate(tokens)
        if w not in _TITLE_STOPWORDS
        and not (w in _VOLUME_MARKERS.values()
                 and i + 1 < len(tokens) and tokens[i + 1].isdecimal()))


def _title_numbers(value):
    """The numbers in a title that tell one volume from another: every bare
    number (so years count too) plus a number word or roman numeral after a
    volume marker. An ordinal such as "60th" is not one (fork #2479)."""
    return {int(w) for w in _title_tokens(value) if w.isdecimal()}


def _volume_numbers_conflict(left, right):
    """True when both titles carry numbers and they differ, as in "One Piece,
    Vol. 12" against "Vol. 13": 0.94 similar as text, same author, different
    book. A title with a number on one side only is left to the other checks
    (fork #2479)."""
    a, b = _title_numbers(left), _title_numbers(right)
    return bool(a and b and a != b)


def _title_similarity(left, right):
    """0.0-1.0 similarity of two titles after normalization (fork #1164).

    1.0 exactly when the identifying words are the same in the same order —
    that is the only signal trusted to mean "the same book". Anything else is
    scored on character similarity and must clear ``_TITLE_FUZZY_MATCH_MIN``,
    which is set high enough that a sibling in the same series cannot pass.
    """
    a, b = _normalize_title(left), _normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    words_a, words_b = _title_content_words(left), _title_content_words(right)
    if words_a and words_a == words_b:
        return 1.0
    if _volume_numbers_conflict(left, right):
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


_PLACEHOLDER_AUTHORS = frozenset(("", "unknown"))


def _author_name_tokens(authors):
    """Normalized name words for a book's or candidate's authors (fork #1164).

    Every word is kept rather than just the last one, because name ORDER is not
    dependable here: Calibre stores and sorts authors as "King, Stephen" while
    providers return "Stephen King", so keying on the final word would make
    those two disagree and reject a correct match for most of a library. Words
    of one or two characters are dropped so initials ("J.") neither match nor
    block.

    Accepts ORM author objects and the plain strings providers return. A bare
    string is wrapped rather than iterated, which would otherwise walk it one
    character at a time.
    """
    if isinstance(authors, str):
        authors = [authors]
    tokens = set()
    for author in (authors or []):
        name = author if isinstance(author, str) else getattr(author, "name", "")
        # Calibre's "Unknown" placeholder is not a name (same rule as
        # _has_meaningful_authors). Counted as one, it disagreed with every
        # candidate, so a book with no author could never be filled in.
        if (name or "").strip().lower() in _PLACEHOLDER_AUTHORS:
            continue
        tokens.update(w for w in _normalize_title(name).split() if len(w) > 2)
    return tokens


def _authors_agree(book_authors, candidate_authors):
    """True when the book and candidate share any author name word (fork #1164).
    Absence of author information on either side is not agreement.

    Deliberately generous: this signal is only ever used to REJECT an otherwise
    matching title, so a shared given name costing us a rejection is far cheaper
    than a name-order mismatch costing every correct match in a library.
    """
    return bool(_author_name_tokens(book_authors) & _author_name_tokens(candidate_authors))


def _select_metadata_result(results, book_isbn, book_title=None, book_authors=None):
    """Pick the provider result that is actually the same book.

    ISBN wins outright when the book carries one (fork #402). Otherwise — the
    normal case for a freshly ingested EPUB, which has no ISBN — the candidates
    are scored on title similarity and the BEST one is returned, but only if it
    clears the confidence bar. ``None`` means "no candidate is plausibly this
    book"; the caller must then apply nothing rather than fall back to a guess.

    Before fork #1164 this fell through to ``results[0]`` whenever ISBN could not
    decide, so whatever the provider ranked first was applied unconditionally —
    which is how a search for "The Devils" overwrote the book with "The Heretics".

    ``book_title``/``book_authors`` default to ``None`` for backwards
    compatibility; when no title is supplied the legacy first-result behaviour is
    kept, so the gate is only ever as strong as the caller. ``fetch_and_apply_
    metadata`` always passes them and a test pins that call site.
    """
    if book_isbn:
        target = _normalize_isbn(book_isbn)
        for result in results:
            identifiers = getattr(result, "identifiers", None) or {}
            for key in ("isbn", "isbn13", "isbn_13", "isbn10"):
                candidate = identifiers.get(key)
                if candidate and _normalize_isbn(candidate) == target:
                    return result

    if not book_title:
        return results[0]

    best = None
    best_score = 0.0
    for result in results:
        score = _title_similarity(book_title, getattr(result, "title", ""))
        if score > best_score:
            best, best_score = result, score

    if best is None:
        return None

    if best_score < 1.0 and best_score < _TITLE_FUZZY_MATCH_MIN:
        log.info(
            "Rejected metadata for %r: closest candidate %r scored %.2f, below "
            "the confidence bar — applying nothing rather than a different book",
            book_title, getattr(best, "title", ""), best_score,
        )
        return None

    # Titles can collide across genuinely different books. When both sides name
    # an author and none of them agree, that's a different book, not an edition.
    candidate_authors = getattr(best, "authors", None)
    if (_author_name_tokens(book_authors) and _author_name_tokens(candidate_authors)
            and not _authors_agree(book_authors, candidate_authors)):
        log.info(
            "Rejected metadata for %r: candidate %r matches on title but its "
            "author differs — applying nothing rather than a different book",
            book_title, getattr(best, "title", ""),
        )
        return None

    return best


def _has_meaningful_title(book):
    """True if the book has a title other than empty or Calibre's 'Unknown'."""
    title = (getattr(book, "title", "") or "").strip().lower()
    return title not in ("", "unknown")


def _has_meaningful_description(text):
    """True if the description has visible text. Markup with no text in it
    (``<p></p>``, ``&nbsp;``) counts as missing, so smart mode can fill it."""
    visible = re.sub(r"<[^>]*>", " ", text or "")
    visible = re.sub(r"&nbsp;|&#160;|\u00a0", " ", visible)
    return bool(visible.strip())


def _has_meaningful_authors(book):
    """True if the book already has a real author (not empty / not the Calibre
    'Unknown' placeholder), so smart mode won't overwrite it (fork #403)."""
    authors = getattr(book, "authors", None) or []
    return any((getattr(a, "name", "") or "").strip().lower() not in _PLACEHOLDER_AUTHORS
               for a in authors)


def _has_pubdate(book):
    """True if the book already has a real publication date (not the Calibre
    'undefined' sentinel year 101), so smart mode won't overwrite it (fork #403)."""
    pubdate = getattr(book, "pubdate", None)
    return bool(pubdate) and getattr(pubdate, "year", 0) > 101


def _parse_metadata_providers_enabled(raw_value):
    """Lightweight parser for metadata_providers_enabled without importing cwa_functions."""
    try:
        if raw_value is None:
            return {}
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode('utf-8', errors='ignore')
        if isinstance(raw_value, str):
            s = raw_value.strip()
            if not s:
                return {}
            if s.startswith("'") and s.endswith("'"):
                s = s[1:-1]
            if not s:
                return {}
            data = json.loads(s)
            return data if isinstance(data, dict) else {}
        if isinstance(raw_value, dict):
            return raw_value
        return {}
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return {}
