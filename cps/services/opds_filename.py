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
from ..unicode_collation import unicode_sort_key
from ..utils.filename_sanitizer import get_valid_filename_shared


log = logger.create()
MAX_TEMPLATE_LENGTH = 1024
MAX_FILENAME_LENGTH = 128
# Calibre's send/save configuration default (not the helper's '%b %Y').
DEFAULT_TIMEFMT = '%b, %Y'
_FIELDS = frozenset((
    'author', 'author_sort', 'authors', 'id', 'identifiers', 'isbn', 'languages',
    'last_modified', 'pubdate', 'publisher', 'rating', 'series', 'series_index',
    'series_sort', 'tags', 'timestamp', 'title', 'title_sort',
))
_FIELD = re.compile(r'([a-zA-Z_]+|#[a-zA-Z][a-zA-Z0-9_]*)(?:\[([0-9]{1,3})\])?\Z')
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
        if not match or (match[1].lower() not in _FIELDS and not match[1].startswith('#')):
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
        # Calibre's filename Formatter normalizes field names and replaces
        # separators in field values before indexing or applying a format.
        value = values[name.lower()].replace('/', '_').replace('\\', '_')
        if index is not None:
            index = int(index)
            value = value[index:index + 1]
        result.append(format(value[:MAX_FILENAME_LENGTH], spec) if value else '')
    return re.sub(r'\s+', ' ', ''.join(result)).strip(' ')


def _finite_number(value):
    """Keep the existing resource bounds without changing valid number text."""
    if value is None or value == '' or len(str(value)) > MAX_FILENAME_LENGTH:
        return None
    try:
        number = Decimal(str(value))
        if number.is_finite() and abs(number.adjusted()) <= MAX_FILENAME_LENGTH:
            return number
    except InvalidOperation:
        pass
    return None


def _series_index(value):
    """Mirror calibre.ebooks.metadata.fmt_sidx, including float rounding."""
    number = _finite_number(value)
    if number is None:
        return ''
    number = float(number)
    if int(number) == number:
        return str(int(number))
    return ('%.2f' % number).rstrip('0')


def _rating(value):
    number = _finite_number(value)
    return str(float(number) / 2.0) if number is not None else ''


def _custom_number(value):
    number = _finite_number(value)
    # Calibre suppresses numeric zero, but not a text column containing '0'.
    return str(value) if number is not None and number != 0 else ''


def _date(value, timefmt, *, local=False):
    if value is None or value == '':
        return ''
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, (date, datetime)):
        return ''
    if not local and (value.year < 101 or (value.year, value.month, value.day) == (101, 1, 1)):
        return ''
    if local and isinstance(value, datetime):
        # Calibre's as_local_time assumes UTC for naive custom dates. Standard
        # dates instead use their existing calendar components unchanged.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone()
    return value.strftime(timefmt)


class _BookValues(dict):
    def __init__(self, book, session, title_regex=None, *,
                 timefmt=DEFAULT_TIMEFMT, title_series_sorting=None):
        # Keep the sorting arguments for callers, but never change field meaning
        # for filenames. Only explicit *_sort fields use stored sort values.
        self.book = book
        self.session = session
        self.timefmt = timefmt
        self.columns = None
        self.depth = 0
        series = book.series[0] if book.series else None
        authors = ' & '.join(author.name.replace('|', ',').replace('&', '&&')
                             for author in book.authors if author.name)
        identifiers = {item.type: item.val for item in getattr(book, 'identifiers', ())}
        super().__init__(
            title=book.title or '', title_sort=book.sort or '',
            author_sort=book.author_sort or '', author=authors, authors=authors,
            id=str(book.id), isbn=book.isbn or '',
            identifiers=', '.join('%s:%s' % (key, identifiers[key]) for key in sorted(identifiers)),
            languages=','.join(language.lang_code for language in book.languages),
            last_modified=_date(book.last_modified, timefmt),
            pubdate=_date(book.pubdate, timefmt), timestamp=_date(book.timestamp, timefmt),
            publisher=', '.join(publisher.name for publisher in book.publishers),
            rating=_rating(book.ratings[0].rating) if book.ratings else '',
            series=(series.name or '') if series else '',
            series_sort=(series.sort or '') if series else '',
            series_index=_series_index(book.series_index) if series else '',
            # Reuse the application's bounded Unicode collation. This is not
            # Calibre's locale-tailored ICU collator (see the OPDS docs).
            tags=', '.join(sorted((tag.name for tag in book.tags), key=unicode_sort_key)).removeprefix('/'),
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
                column.label.lower(): column for column in self.session.query(db.CustomColumns).all()
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
        if index:
            values = [_series_index(v) for v in values]
        elif column.datatype == 'datetime':
            values = [_date(v, self.timefmt, local=True) for v in values]
        elif column.datatype == 'bool':
            # Stable English filename profile, independent of request locale.
            values = [('yes' if v else 'no') if v is not None else '' for v in values]
        elif column.datatype == 'rating':
            values = [_rating(v) for v in values]
        elif column.datatype in ('int', 'float'):
            values = [_custom_number(v) for v in values]
        return ','.join(str(v) for v in values if v is not None)


def render_filename(template, book, session, title_regex=None, unicode_filename=False,
                    *, timefmt=DEFAULT_TIMEFMT, title_series_sorting=None):
    """Return a safe basename. The download helper adds the actual extension."""
    values = _BookValues(book, session, title_regex, timefmt=timefmt,
                         title_series_sorting=title_series_sorting)
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
