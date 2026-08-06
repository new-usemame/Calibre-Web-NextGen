# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import zipfile
from collections import OrderedDict
from threading import RLock
from lxml import etree

from . import isoLanguages, cover
from . import config, logger
from .helper import split_authors
from .epub_helper import get_content_opf, default_ns
from .constants import BookMeta
from .string_helper import strip_whitespaces

log = logger.create()

# 4096 entries covers forty full Kobo sync pages while keeping the process-local
# memo to a few MiB even for libraries much larger than the usual deployment.
_EPUB_LAYOUT_CACHE_MAX = 4096
_epub_layout_cache = OrderedDict()
_epub_layout_cache_lock = RLock()
_CACHE_MISS = object()


def _clear_epub_layout_cache():
    """Clear process-local state (primarily for tests)."""
    with _epub_layout_cache_lock:
        _epub_layout_cache.clear()


def _cached_epub_layout(key):
    with _epub_layout_cache_lock:
        layout = _epub_layout_cache.get(key, _CACHE_MISS)
        if layout is not _CACHE_MISS:
            _epub_layout_cache.move_to_end(key)
        return layout


def _remember_epub_layout(key, layout):
    with _epub_layout_cache_lock:
        _epub_layout_cache[key] = layout
        _epub_layout_cache.move_to_end(key)
        while len(_epub_layout_cache) > _EPUB_LAYOUT_CACHE_MAX:
            _epub_layout_cache.popitem(last=False)


def _extract_cover(zip_file, cover_file, cover_path, tmp_file_name):
    if cover_file is None:
        return None

    cf = extension = None
    zip_cover_path = os.path.join(cover_path, cover_file).replace('\\', '/')

    prefix = os.path.splitext(tmp_file_name)[0]
    tmp_cover_name = prefix + '.' + os.path.basename(zip_cover_path)
    ext = os.path.splitext(tmp_cover_name)
    if len(ext) > 1:
        extension = ext[1].lower()
    if extension in cover.COVER_EXTENSIONS:
        cf = zip_file.read(zip_cover_path)
    return cover.cover_processing(tmp_file_name, cf, extension)


def get_epub_layout(book, book_data):
    file_path = os.path.normpath(os.path.join(config.get_book_path(),
                                              book.path, book_data.name + "." + book_data.format.lower()))

    try:
        file_stat = os.stat(file_path)
        cache_key = (file_path, file_stat.st_mtime_ns, file_stat.st_size)
        layout = _cached_epub_layout(cache_key)
        if layout is not _CACHE_MISS:
            return layout

        tree, __ = get_content_opf(file_path, default_ns)
        p = tree.xpath('/pkg:package/pkg:metadata', namespaces=default_ns)[0]

        layout = p.xpath('pkg:meta[@property="rendition:layout"]/text()', namespaces=default_ns)
    except (etree.XMLSyntaxError, KeyError, IndexError, OSError,
            zipfile.BadZipFile, RuntimeError) as e:
        # BadZipFile subclasses Exception, not OSError, so a truncated or
        # otherwise corrupt archive used to escape this handler even though
        # "unparseable epub" is exactly what it means -- the one caller that
        # noticed wrapped this call in its own try (cps/kobo.py). RuntimeError
        # is the same shape for a password-protected archive -- note it is the
        # broadest clause here, so it also absorbs NotImplementedError
        # (unsupported compression or zip version, which is still "unparseable
        # epub") and RecursionError from lxml on a pathological tree. Every
        # caller already treats None as "layout unknown", so report it here once
        # instead of leaking two more exception types to each call site.
        log.error("Could not parse epub metadata of book {} during kobo sync: {}".format(book.id, e))
        return None

    result = layout[0] if layout else None
    _remember_epub_layout(cache_key, result)
    return result


def get_epub_info(tmp_file_path, original_file_name, original_file_extension, no_cover_processing):
    ns = {
        'n': 'urn:oasis:names:tc:opendocument:xmlns:container',
        'pkg': 'http://www.idpf.org/2007/opf',
        'dc': 'http://purl.org/dc/elements/1.1/'
    }

    tree, cf_name = get_content_opf(tmp_file_path, ns)

    cover_path = os.path.dirname(cf_name)

    p = tree.xpath('/pkg:package/pkg:metadata', namespaces=ns)[0]

    epub_metadata = {}

    for s in ['title', 'description', 'creator', 'language', 'subject', 'publisher', 'date']:
        tmp = p.xpath('dc:%s/text()' % s, namespaces=ns)
        if len(tmp) > 0:
            if s == 'creator':
                epub_metadata[s] = ' & '.join(split_authors(tmp))
            elif s == 'subject':
                epub_metadata[s] = ', '.join(tmp)
            elif s == 'date':
                epub_metadata[s] = tmp[0][:10]
            else:
                epub_metadata[s] = strip_whitespaces(tmp[0])
        else:
            epub_metadata[s] = 'Unknown'

    if epub_metadata['subject'] == 'Unknown':
        epub_metadata['subject'] = ''

    if epub_metadata['publisher'] == 'Unknown':
        epub_metadata['publisher'] = ''

    if epub_metadata['date'] == 'Unknown':
        epub_metadata['date'] = ''

    if epub_metadata['description'] == 'Unknown':
        description = tree.xpath("//*[local-name() = 'description']/text()")
        if len(description) > 0:
            epub_metadata['description'] = description
        else:
            epub_metadata['description'] = ""

    lang = epub_metadata['language'].split('-', 1)[0].lower()
    epub_metadata['language'] = isoLanguages.get_lang3(lang)

    epub_metadata = parse_epub_series(ns, tree, epub_metadata)

    epub_zip = zipfile.ZipFile(tmp_file_path)
    if not no_cover_processing:
        cover_file = parse_epub_cover(ns, tree, epub_zip, cover_path, tmp_file_path)
    else:
        cover_file = None

    identifiers = []
    for node in p.xpath('dc:identifier', namespaces=ns):
        try:
            identifier_name = node.attrib.values()[-1]
        except IndexError:
            continue
        identifier_value = node.text
        if identifier_name in ('uuid', 'calibre') or identifier_value is None:
            continue
        identifiers.append([identifier_name, identifier_value])

    if not epub_metadata['title']:
        title = original_file_name
    else:
        title = epub_metadata['title']

    return BookMeta(
        file_path=tmp_file_path,
        extension=original_file_extension,
        title=title.encode('utf-8').decode('utf-8'),
        author=epub_metadata['creator'].encode('utf-8').decode('utf-8'),
        cover=cover_file,
        description=epub_metadata['description'],
        tags=epub_metadata['subject'].encode('utf-8').decode('utf-8'),
        series=epub_metadata['series'].encode('utf-8').decode('utf-8'),
        series_id=epub_metadata['series_id'].encode('utf-8').decode('utf-8'),
        languages=epub_metadata['language'],
        publisher=epub_metadata['publisher'].encode('utf-8').decode('utf-8'),
        pubdate=epub_metadata['date'],
        identifiers=identifiers)


def parse_epub_cover(ns, tree, epub_zip, cover_path, tmp_file_path):
    cover_section = tree.xpath("/pkg:package/pkg:manifest/pkg:item[@id='cover-image']/@href", namespaces=ns)
    for cs in cover_section:
        cover_file = _extract_cover(epub_zip, cs, cover_path, tmp_file_path)
        if cover_file:
            return cover_file

    meta_cover = tree.xpath("/pkg:package/pkg:metadata/pkg:meta[@name='cover']/@content", namespaces=ns)
    if len(meta_cover) > 0:
        cover_section = tree.xpath(
            "/pkg:package/pkg:manifest/pkg:item[@id='"+meta_cover[0]+"']/@href", namespaces=ns)
        if not cover_section:
            cover_section = tree.xpath(
                "/pkg:package/pkg:manifest/pkg:item[@properties='" + meta_cover[0] + "']/@href", namespaces=ns)
    else:
        cover_section = tree.xpath("/pkg:package/pkg:guide/pkg:reference/@href", namespaces=ns)

    cover_file = None
    for cs in cover_section:
        if cs.endswith('.xhtml') or cs.endswith('.html'):
            markup = epub_zip.read(os.path.join(cover_path, cs))
            markup_tree = etree.fromstring(markup, parser=etree.XMLParser(resolve_entities=False, no_network=True))
            # no matter xhtml or html with no namespace
            img_src = markup_tree.xpath("//*[local-name() = 'img']/@src")
            # Alternative image source
            if not len(img_src):
                img_src = markup_tree.xpath("//attribute::*[contains(local-name(), 'href')]")
            if len(img_src):
                # img_src maybe start with "../"" so fullpath join then relpath to cwd
                filename = os.path.relpath(os.path.join(os.path.dirname(os.path.join(cover_path, cover_section[0])),
                                                        img_src[0]))
                cover_file = _extract_cover(epub_zip, filename, "", tmp_file_path)
        else:
            cover_file = _extract_cover(epub_zip, cs, cover_path, tmp_file_path)
        if cover_file:
            break
    return cover_file


def parse_epub_series(ns, tree, epub_metadata):
    series = tree.xpath("/pkg:package/pkg:metadata/pkg:meta[@name='calibre:series']/@content", namespaces=ns)
    if len(series) > 0:
        epub_metadata['series'] = series[0]
    else:
        epub_metadata['series'] = ''

    series_id = tree.xpath("/pkg:package/pkg:metadata/pkg:meta[@name='calibre:series_index']/@content", namespaces=ns)
    if len(series_id) > 0:
        epub_metadata['series_id'] = series_id[0]
    else:
        # Absence is '', not a fabricated '1' — consumers that want a default
        # apply their own (upload already creates Books with series_index '1'
        # and edit_book_series_index no-ops on falsy; merge_metadata skips
        # falsy). Fabricating '1' here made reload_metadata_from_disk stomp a
        # curated series_index whenever a file carried a series name without
        # a calibre:series_index meta (#218 follow-up review).
        epub_metadata['series_id'] = ''
    return epub_metadata
