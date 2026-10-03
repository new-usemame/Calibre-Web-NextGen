#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate filename expectations with Calibre, never with the server renderer.

Run with an isolated Calibre configuration, English locale, and UTC:
    CALIBRE_CONFIG_DIRECTORY="$(mktemp -d)" CALIBRE_OVERRIDE_LANG=en \
      LC_ALL=C TZ=UTC calibre-debug -e scripts/generate_calibre_filename_fixtures.py

Pass -- --check to compare the committed fixture without rewriting it.
Ordinary unit tests read the fixture and do not need Calibre installed.
Use strictly_alphabetic to compare original title and series values. The server
ignores sorting tweaks and requires {title_sort} for a sorted title.
"""
import argparse
import copy
import json
import os
from datetime import datetime
from pathlib import Path

from calibre.constants import __version__
from calibre.ebooks.metadata.book.base import Metadata
from calibre.library.save_to_disk import Formatter, get_component_metadata
from calibre.utils.config_base import tweaks


BOOK = {
    'id': 42, 'title': 'The Book', 'title_sort': 'Book, The',
    'author_sort': 'Writer, Ann & Reader, Ben',
    'authors': ['Ann Writer', 'Ben Reader'],
    'identifiers': {'isbn': '9781234567890', 'doi': '10/example'},
    'languages': ['eng', 'fra'], 'pubdate': '2020-05-06T12:00:00+00:00',
    'timestamp': '2024-01-02T03:04:05+00:00',
    'last_modified': '2024-02-03T04:05:06+00:00',
    'publisher': 'Press', 'rating': 8, 'series': 'The Saga',
    # Deliberately inconsistent: Calibre prepares from series, not series_sort.
    'series_sort': 'DO NOT USE THE STORED SERIES SORT',
    'series_index': 2.345, 'tags': ['Space', 'Fiction'],
}
CUSTOM = {
    'shelf': {'datatype': 'text', 'value': ['Favorites', 'Other'], 'multiple': True},
    'count': {'datatype': 'int', 'value': 0},
    'fraction': {'datatype': 'float', 'value': 2.0},
    'read': {'datatype': 'bool', 'value': False},
    'date': {'datatype': 'datetime', 'value': '2020-05-06T12:00:00+00:00'},
    'saga': {'datatype': 'series', 'value': 'The Cycle', 'extra': 2.345},
    'stars': {'datatype': 'rating', 'value': 8},
    'textzero': {'datatype': 'text', 'value': '0'},
    'computed': {'datatype': 'composite', 'template': '{#shelf} {title}'},
}


def case(name, template, *, book=None, custom=None, **profile):
    return dict(name=name, template=template, book=book or {}, custom=custom or {},
                profile=profile)


CASES = [
    case(field, '{' + field + '}') for field in (
        'title', 'title_sort', 'author', 'authors', 'author_sort', 'id',
        'isbn', 'identifiers', 'languages', 'pubdate', 'timestamp',
        'last_modified', 'publisher', 'rating', 'series', 'series_index', 'tags',
        '#shelf', '#count', '#fraction', '#read', '#date', '#saga', '#saga_index',
        '#stars', '#textzero', '#computed', '#missing',
    )
] + [
    case('case_insensitive', '{TITLE} {#SAGA}'),
    case('first_character', '{author_sort[0]}'),
    case('literal_braces', '{{title}} {title}'),
    case('space_padding', 'x{series_index:>5s}x', book={'series_index': 2}),
    case('zero_padding', '{series_index:0>3s}', book={'series_index': 2}),
    case('zero_custom_padding', 'x{#count:0>3s}x'),
    case('zero_series', '{series_index:0>3s}', book={'series_index': 0}),
    case('rounding_up', '{series_index}', book={'series_index': 1.999}),
    case('small_fraction', 'x{series_index}x', book={'series_index': 2.001}),
    case('negative_fraction', '{series_index}', book={'series_index': -2.345}),
    case('rating_zero', '{rating}', book={'rating': 0}),
    case('rating_missing', 'x{rating:0>3s}x', book={'rating': None}),
    case('fractional_rating', '{rating}', book={'rating': 9}),
    case('no_series', 'x{series}{series_index:0>3s}x',
         book={'series': None, 'series_index': None}),
    case('missing_values', 'x{pubdate}{timestamp}{last_modified}{authors}{tags}{languages}x',
         book={'pubdate': None, 'timestamp': None, 'last_modified': None,
               'authors': [], 'tags': [], 'languages': []}),
    case('undefined_pubdate', 'x{pubdate}x', book={'pubdate': '0101-01-01T00:00:00+00:00'}),
    case('title_fallback', '{title}', book={'title_sort': None}),
    case('quoted_title', '{title}', book={'title': '“The Book”', 'title_sort': None}),
    case('quoted_series', '{series}', book={'series': '“The Saga”'}),
    case('article_before_quote', '{title}', book={'title': 'The "Book"', 'title_sort': None}),
    case('custom_article_before_quote', '{#saga}', custom={'saga': {'value': 'The "Cycle"'}}),
    case('custom_title_sort', '{title}', book={'title_sort': 'My chosen sort'}),
    case('explicit_title_sort', '{title_sort}', book={'title_sort': 'My chosen sort'}),
    case('default_template', '{title_sort} - {authors}'),
    case('original_title_template', '{title} - {authors}'),
    case('alphabetic', '{title} {series} {#saga}', title_series_sorting='strictly_alphabetic'),
    case('helper_date_format', '{pubdate} {#date}', timefmt='%b %Y'),
    case('explicit_date_format', '{pubdate} {#date}', timefmt='%Y-%m-%d'),
    case('date_timezone', '{pubdate} {#date}', timefmt='%Y-%m-%d',
         book={'pubdate': '2020-05-06T00:30:00+02:00'},
         custom={'date': {'value': '2020-05-06T00:30:00+02:00'}}),
    case('naive_custom_date', '{#date}', custom={'date': {'value': '2020-05-06T12:00:00'}}),
    case('true_boolean', '{#read}', custom={'read': {'value': True}}),
    case('missing_boolean', 'x{#read}x', custom={'read': {'value': None}}),
    case('float_zero', 'x{#fraction:0>3s}x', custom={'fraction': {'value': 0.0}}),
    case('custom_rating_zero', '{#stars}', custom={'stars': {'value': 0}}),
    case('custom_series_quotes', '{#saga}', custom={'saga': {'value': '“The Cycle”'}}),
    case('author_escaping', '{authors}', book={'authors': ['A & B', 'Last, First']}),
    case('tag_order', '{tags}', book={'tags': ['zebra', 'Alpha', 'beta']}),
    case('tag_leading_slash', '{tags}', book={'tags': ['/Fiction']}),
    case('metadata_slashes', '{title} {title[1]}', book={'title': 'A/B\\C'}),
    case('whitespace', '  x  {title} \t y  ', book={'title': 'Book\n  Title'}),
    case('string_precision', '{title:.4s}'),
]


def to_metadata(book, custom):
    mi = Metadata(book['title'], book['authors'])
    # The constructor substitutes ['Unknown'] for empty authors. Explicitly
    # set the supplied fields so both renderers receive the same metadata.
    for key in ('title_sort', 'authors', 'author_sort', 'languages', 'publisher', 'rating',
                'series', 'series_index', 'tags', 'identifiers'):
        setattr(mi, key, book[key])
    for key in ('pubdate', 'timestamp', 'last_modified'):
        setattr(mi, key, datetime.fromisoformat(book[key]) if book[key] else None)
    for label, column in custom.items():
        value = column.get('value')
        if column['datatype'] == 'datetime' and value:
            value = datetime.fromisoformat(value)
        mi.set_user_metadata('#' + label, {
            'datatype': column['datatype'], 'name': label, 'label': label,
            'display': {'composite_template': column.get('template', '')},
            'is_multiple': {'list_to_ui': ', '} if column.get('multiple') else {},
            '#value#': value, '#extra#': column.get('extra'),
        })
    return mi


def generate():
    tweaks['default_language_for_title_sort'] = 'eng'
    records = []
    for item in CASES:
        book = dict(BOOK, **item['book'])
        custom = copy.deepcopy(CUSTOM)
        for label, updates in item['custom'].items():
            custom[label].update(updates)
        profile = dict(timefmt='%b, %Y', title_series_sorting='strictly_alphabetic')
        profile.update(item['profile'])
        tweaks['save_template_title_series_sorting'] = profile['title_series_sorting']
        mi = to_metadata(book, custom)
        values = get_component_metadata(item['template'], mi, book['id'],
                                        timefmt=profile['timefmt'])
        expected = Formatter().unsafe_format(item['template'], values, mi)
        records.append(dict(item, expected=expected))
    return {
        'generator': 'scripts/generate_calibre_filename_fixtures.py',
        'calibre_version': __version__, 'locale': 'en', 'timezone': 'UTC',
        'defaults': {'timefmt': '%b, %Y', 'title_series_sorting': 'strictly_alphabetic'},
        'book': BOOK, 'custom': CUSTOM, 'cases': records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path, default=(
        Path(__file__).resolve().parents[1] / 'tests/fixtures/calibre_filename_templates.json'))
    args = parser.parse_args()
    if any(os.environ.get(key) != value for key, value in (
            ('CALIBRE_OVERRIDE_LANG', 'en'), ('LC_ALL', 'C'), ('TZ', 'UTC'))):
        parser.error('Run with CALIBRE_OVERRIDE_LANG=en LC_ALL=C TZ=UTC.')
    if not os.environ.get('CALIBRE_CONFIG_DIRECTORY'):
        parser.error('Set CALIBRE_CONFIG_DIRECTORY to an isolated configuration directory.')
    data = generate()
    if args.check:
        old = json.loads(args.output.read_text())
        if old != data:
            raise SystemExit('Calibre output differs. Regenerate and review the fixture changes.')
        print('%d Calibre cases match the committed fixture.' % len(data['cases']))
    else:
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        print('Wrote %d Calibre %s cases to %s' % (len(data['cases']), __version__, args.output))


if __name__ == '__main__':
    main()
