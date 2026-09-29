# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A bounded metadata template for OPDS basenames, not a Python evaluator.

This supports Calibre-style field substitutions, character indexing and string
padding. It does not execute Calibre template functions or program mode.
"""
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from string import Formatter

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .. import db, logger
from ..utils.filename_sanitizer import get_valid_filename_shared


log = logger.create()
MAX_TEMPLATE_LENGTH = 1024
MAX_FILENAME_LENGTH = 128
_FIELDS = frozenset((
    'author_sort', 'authors', 'id', 'isbn', 'languages', 'last_modified',
    'pubdate', 'publisher', 'rating', 'series', 'series_index', 'tags',
    'timestamp', 'title',
))
_FIELD = re.compile(r'([a-z_]+|#[a-zA-Z][a-zA-Z0-9_]*)(?:\[([0-9]{1,3})\])?\Z')
_FORMAT = re.compile(r'(?:(.[<^>]|[<^>]))?([0-9]{1,3})?(?:\.([0-9]{1,3}))?s?\Z')
_UNSAFE = re.compile(r'[\x00-\x1f\x7f-\x9f/\\:*?"<>|]')
_RESERVED = re.compile(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def _parts(template):
    if not isinstance(template, str) or len(template) > MAX_TEMPLATE_LENGTH:
        raise ValueError('Use a text template of at most 1024 characters.')
    try:
        parts = list(Formatter().parse(template))
    except ValueError:
        raise ValueError('Unmatched braces in the OPDS filename template.') from None
    for literal, field, spec, conversion in parts:
        if field is None:
            continue
        match = _FIELD.fullmatch(field)
        if not match or (match[1] not in _FIELDS and not match[1].startswith('#')):
            raise ValueError('Unknown or unsupported OPDS filename field: %s' % field)
        if conversion:
            raise ValueError('Conversions such as !r are not supported in OPDS filenames.')
        fmt = _FORMAT.fullmatch(spec)
        if not fmt or any(int(n) > MAX_FILENAME_LENGTH for n in fmt.groups()[1:] if n):
            raise ValueError('Use string padding such as 0>3s, with a maximum width of 128.')
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
        result.append(format(value[:MAX_FILENAME_LENGTH], spec) if value else '')
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
                value = value.astimezone(timezone.utc)
            value = value.date()
        return value.isoformat()
    return str(value)


def _sorted_name(name, stored_sort, title_regex):
    if not name:
        return ''
    if stored_sort:
        return stored_sort
    try:
        match = re.match(title_regex, name, re.IGNORECASE) if title_regex else None
        if match and match.lastindex:
            article = match.group(1)
            return name[len(article):].strip() + ', ' + article
    except re.error:
        pass
    return name


class _BookValues(dict):
    def __init__(self, book, session, title_regex):
        self.book = book
        self.session = session
        self.columns = None
        self.depth = 0
        series = book.series[0] if book.series else None
        super().__init__(
            title=_sorted_name(book.title, book.sort, title_regex),
            author_sort=book.author_sort or '',
            authors=' & '.join(author.name.replace('|', ',') for author in book.authors),
            id=_value(book.id), isbn=book.isbn or '',
            languages=', '.join(language.lang_code for language in book.languages),
            last_modified=_value(book.last_modified), pubdate=_value(book.pubdate),
            timestamp=_value(book.timestamp),
            publisher=', '.join(publisher.name for publisher in book.publishers),
            rating=_number(book.ratings[0].rating / 2) if book.ratings and book.ratings[0].rating else '',
            series=_sorted_name(series.name, series.sort, title_regex) if series else '',
            series_index=_number(book.series_index) if series else '',
            tags=', '.join(tag.name for tag in book.tags),
        )

    def __missing__(self, key):
        # Install the empty value first to break cycles in composite columns.
        self[key] = ''
        if not key.startswith('#') or self.depth >= 10:
            return ''
        self.depth += 1
        try:
            self[key] = self._custom_value(key[1:])
        except (SQLAlchemyError, ValueError, TypeError, KeyError, InvalidOperation):
            log.warning('Could not read custom field %s for an OPDS filename', key)
        finally:
            self.depth -= 1
        return self[key]

    def _custom_value(self, label):
        if self.columns is None:
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
        if column.datatype == 'composite':
            display = json.loads(column.display or '{}')
            if not isinstance(display, dict):
                raise ValueError('Invalid custom column display metadata')
            return expand_template(display.get('composite_template', ''), self)

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
                   'ORDER BY value.id') % (value, table, link)
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
        return ', '.join(_value(v) for v in values if v is not None)


def render_filename(template, book, session, title_regex='', unicode_filename=False):
    """Return a safe basename. The download helper adds the actual extension."""
    values = _BookValues(book, session, title_regex)
    rendered = expand_template(template, values).strip(' .') or 'book-%s' % book.id
    rendered = get_valid_filename_shared(
        rendered, replace_whitespace=False, chars=MAX_FILENAME_LENGTH,
        unicode_filename=unicode_filename,
    )
    # Content-Disposition cannot create directories. Sanitize after optional
    # transliteration, which can itself introduce path separators or CON etc.
    rendered = _UNSAFE.sub('_', rendered).strip(' .') or 'book-%s' % book.id
    if _RESERVED.match(rendered):
        rendered = '_' + rendered
    return get_valid_filename_shared(
        rendered, replace_whitespace=False, chars=MAX_FILENAME_LENGTH,
    ).rstrip(' .')
