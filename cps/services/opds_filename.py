# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A bounded metadata template for OPDS basenames, not a Python evaluator.

This supports Calibre-style field substitutions, character indexing and string
padding. It does not execute Calibre template functions or program mode.
"""
import json
import re
import unicodedata
from collections import ChainMap
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from string import Formatter

from flask_babel import gettext as _
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .. import db, logger
from ..string_helper import title_sort_name
from ..utils.filename_sanitizer import get_valid_filename_shared


log = logger.create()
MAX_TEMPLATE_LENGTH = 1024
MAX_FILENAME_LENGTH = 128
MAX_CUSTOM_LOOKUPS = 256
_FIELDS = frozenset((
    'author_sort', 'authors', 'id', 'isbn', 'languages', 'last_modified',
    'pubdate', 'publisher', 'rating', 'series', 'series_index', 'tags',
    'timestamp', 'title',
))
_FIELD = re.compile(r'([a-z_]+|#[a-zA-Z][a-zA-Z0-9_]*)(?:\[([0-9]{1,3})\])?\Z')
_FORMAT = re.compile(r'(?:(.[<^>]|[<^>]))?([0-9]{1,3})?(?:\.([0-9]{1,3}))?s?\Z')
_UNSAFE = re.compile(r'[\x00-\x1f\x7f-\x9f/\\:*?"<>|]')
_RESERVED = re.compile(r'(CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]) *(?:\.|$)', re.I)


def _format_parts(spec):
    parts = spec.split('|')
    if len(parts) not in (1, 3):
        raise ValueError(_('Conditional text needs both prefix and suffix separators.'))
    prefix, suffix = parts[1:] if len(parts) == 3 else ('', '')
    if any(char in prefix + suffix for char in '{}'):
        raise ValueError(_('Nested fields are not supported in conditional text.'))
    return parts[0], prefix, suffix


def _parts(template):
    if not isinstance(template, str) or len(template) > MAX_TEMPLATE_LENGTH:
        raise ValueError(_('Use a text template of at most 1024 characters.'))
    try:
        template.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(_('Use a text template of at most 1024 characters.')) from None
    try:
        parts = list(Formatter().parse(template))
    except ValueError:
        raise ValueError(_('Unmatched braces in the OPDS filename template.')) from None
    for literal, field, spec, conversion in parts:
        if field is None:
            continue
        match = _FIELD.fullmatch(field)
        if not match or (match[1] not in _FIELDS and not match[1].startswith('#')):
            raise ValueError(_('Unknown or unsupported OPDS filename field: %(field)s', field=field))
        if conversion:
            raise ValueError(_('Conversions such as !r are not supported in OPDS filenames.'))
        format_spec, prefix, suffix = _format_parts(spec)
        fmt = _FORMAT.fullmatch(format_spec)
        if not fmt or any(int(n) > MAX_FILENAME_LENGTH for n in fmt.groups()[1:] if n):
            raise ValueError(_('Use string padding such as 0>3s, with a maximum width of 128.'))
        if not fmt[1] and fmt[2] and fmt[2].startswith('0'):
            raise ValueError(_('Use explicit alignment for zero padding, such as 0>3s.'))
    return parts


def validate_template(template):
    """Raise ValueError before either admin editor changes configuration."""
    _parts(template)


def expand_template(template, values):
    """Substitute only approved string values. Missing values bypass padding."""
    result = []
    for literal, field, spec, conversion in _parts(template):
        result.append(literal)
        if field is None:
            continue
        name, index = _FIELD.fullmatch(field).groups()
        value = values[name]
        if index is not None:
            index = int(index)
            value = value[index:index + 1]
        format_spec, prefix, suffix = _format_parts(spec)
        formatted = format(value[:MAX_FILENAME_LENGTH], format_spec) if value else ''
        result.append(prefix + formatted + suffix if formatted else '')
    return ''.join(result)


def _number(value):
    if value is None or value == '':
        return ''
    try:
        if len(str(value)) > MAX_FILENAME_LENGTH:
            return ''
        number = Decimal(str(value))
        if not number.is_finite() or abs(number.adjusted()) > MAX_FILENAME_LENGTH:
            return ''
        fixed = format(number, 'f')
        return fixed.rstrip('0').rstrip('.') if '.' in fixed else fixed
    except InvalidOperation:
        return ''


def _value(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (datetime, date)):
        # Calibre uses year 101 for an unset date.
        if value.year <= 101:
            return ''
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                try:
                    value = value.astimezone(timezone.utc)
                except OverflowError:
                    return ''
            value = value.date()
        return value.isoformat()
    return str(value)


def _sorted_name(name, stored_sort, title_regex):
    if not name:
        return ''
    if stored_sort:
        return stored_sort
    return title_sort_name(name, title_regex)


class _BookValues(dict):
    def __init__(self, book, session, title_regex, ordered_authors=None):
        self.book = book
        self.session = session
        self.columns = None
        self.depth = 0
        self.active = set()
        self.composites = {}
        self.displays = {}
        self.warned = set()
        self.lookups_remaining = MAX_CUSTOM_LOOKUPS
        series = next((item for item in book.series if item is not None), None)
        authors = book.authors if ordered_authors is None else ordered_authors
        # The shared orderer cannot distinguish authors with identical sort
        # strings. Resolve those occurrences in their original link order.
        by_sort = {}
        for author in book.authors:
            if author is not None:
                by_sort.setdefault(getattr(author, 'sort', None), []).append(author)
        collision_offsets = {}
        corrected = []
        for author in authors:
            group = by_sort.get(getattr(author, 'sort', None), [])
            if author is not None and len(group) > 1:
                offset = collision_offsets.get(author.sort, 0)
                if offset < len(group):
                    author = group[offset]
                    collision_offsets[author.sort] = offset + 1
            corrected.append(author)
        authors = corrected
        author_names = []
        seen_authors = set()
        for author in authors:
            if author is None or not author.name:
                continue
            author_id = getattr(author, 'id', None)
            author_key = author_id if author_id is not None else id(author)
            if author_key not in seen_authors:
                seen_authors.add(author_key)
                author_names.append(author.name.replace('|', ','))
        rating = next((item.rating for item in book.ratings if item is not None and item.rating), None)
        super().__init__(
            title=_sorted_name(book.title, book.sort, title_regex),
            author_sort=book.author_sort or '',
            authors=' & '.join(author_names),
            id=_value(book.id), isbn=book.isbn or '',
            languages=', '.join(language.lang_code for language in book.languages if language is not None),
            last_modified=_value(book.last_modified), pubdate=_value(book.pubdate),
            timestamp=_value(book.timestamp),
            publisher=', '.join(publisher.name for publisher in book.publishers if publisher is not None),
            rating=_number(rating / 2) if rating else '',
            series=_sorted_name(series.name, series.sort, title_regex) if series else '',
            series_index=_number(book.series_index) if series else '',
            tags=', '.join(tag.name for tag in book.tags if tag is not None),
        )

    def __getitem__(self, key):
        # Enforce the depth bound even for a leaf resolved earlier in this name.
        if key.startswith('#') and (self.depth >= 10 or key in self.active):
            return ''
        return super().__getitem__(key)

    def __missing__(self, key):
        if not key.startswith('#'):
            return ''
        context = (key, self.depth, frozenset(self.active))
        if context in self.composites:
            return self.composites[context]
        if self.lookups_remaining <= 0:
            return ''
        self.lookups_remaining -= 1
        self.active.add(key)
        self.depth += 1
        value = ''
        try:
            value = self._custom_value(key[1:])
        except (SQLAlchemyError, ValueError, TypeError, KeyError, InvalidOperation, OverflowError, RecursionError):
            if key not in self.warned:
                self.warned.add(key)
                log.warning('Could not read custom field %s for an OPDS filename', key)
        finally:
            self.depth -= 1
            self.active.remove(key)
        column = (self.columns or {}).get(key[1:])
        # Include depth and active ancestors in composite cache keys: a value
        # cut short by a cycle/depth bound must not poison a shallow reference.
        if column is not None and column.datatype == 'composite':
            self.composites[context] = value
        else:
            self[key] = value
        return value

    def _custom_value(self, label):
        if self.columns is None:
            # Cache an unavailable schema as empty for this one download; do
            # not retry the same failed query for every distinct custom field.
            self.columns = {}
            self.columns = {
                column.label: column for column in self.session.query(db.CustomColumns).all()
                if not column.mark_for_delete
            }
        column = self.columns.get(label)
        index = False
        if column is None and label.endswith('_index'):
            column = self.columns.get(label[:-6])
            index = column is not None and column.datatype == 'series'
            if not index:
                return ''
        if column is None:
            return ''
        if column.id not in self.displays:
            display = json.loads(column.display or '{}') if column.datatype == 'composite' or column.is_multiple else {}
            if not isinstance(display, dict):
                raise ValueError('Invalid custom column display metadata')
            self.displays[column.id] = display
        display = self.displays[column.id]
        if column.datatype == 'composite':
            metadata = ChainMap({
                'title': self.book.title or '',
                'series': next((item.name or '' for item in self.book.series if item is not None), ''),
            }, self)
            return expand_template(display.get('composite_template', ''), metadata)

        # IDs come from the schema, not template text. Values stay bound.
        # Read the actual Calibre tables because CWNG does not map custom
        # series columns to Books relationships. This also covers all stored
        # custom types without changing the library or its ORM mappings.
        column_id = int(column.id)
        table = 'custom_column_%d' % column_id
        if column.normalized:
            link = 'books_custom_column_%d_link' % column_id
            value = 'link.extra' if index else 'value.value'
            sql = ('SELECT %s FROM %s AS value JOIN %s AS link '
                   'ON value.id = link.value WHERE link.book = :book_id '
                   'ORDER BY link.rowid') % (value, table, link)
        else:
            sql = 'SELECT value FROM %s WHERE book = :book_id ORDER BY id' % table
        values = self.session.execute(text(sql), {'book_id': self.book.id}).scalars().all()
        if column.datatype == 'datetime':
            values = [datetime.fromisoformat(v.replace('Z', '+00:00'))
                      if isinstance(v, str) and v else v for v in values]
        elif column.datatype == 'bool':
            values = [bool(v) if v is not None else None for v in values]
        elif column.datatype == 'rating':
            values = [_number(Decimal(str(v)) / 2) if v else '' for v in values]
        elif index or column.datatype in ('int', 'float'):
            values = [_number(v) for v in values]
        separator = ' & ' if column.is_multiple and display.get('is_names') else ', '
        return separator.join(_value(v) for v in values if v is not None)


def render_filename(template, book, session, title_regex='', unicode_filename=False, ordered_authors=None):
    """Return a safe basename. The download helper adds the actual extension."""
    values = _BookValues(book, session, title_regex, ordered_authors)
    rendered = expand_template(template, values)
    rendered = ''.join(char for char in rendered if unicodedata.category(char) not in ('Cc', 'Zl', 'Zp')
                       and (unicodedata.category(char) != 'Cf' or char in ('\u200c', '\u200d')))
    rendered = rendered.strip().strip(' .') or 'book-%s' % book.id
    try:
        rendered = get_valid_filename_shared(
            rendered, replace_whitespace=False, chars=MAX_FILENAME_LENGTH,
            unicode_filename=unicode_filename,
        )
    except ValueError:
        rendered = 'book-%s' % book.id
    # Content-Disposition cannot create directories. Sanitize after optional
    # transliteration, which can itself introduce path separators or CON etc.
    rendered = _UNSAFE.sub('_', rendered).strip(' .') or 'book-%s' % book.id
    if _RESERVED.match(rendered):
        rendered = '_' + rendered
    return get_valid_filename_shared(
        rendered, replace_whitespace=False, chars=MAX_FILENAME_LENGTH,
    ).rstrip(' .')
