# Calibre filename template compatibility

This document describes Calibre's rules for filenames from templates. Use it when another application must produce the same filename for the same book. It includes standalone tests and does not require a particular application repository.

The target is Calibre's save-to-disk and send-to-device behavior. The target is not every use of the Template Editor. A template can produce different text when Calibre prepares its metadata for a different task.

CWNG intentionally differs from Calibre's default filename sorting tweak. It keeps original title and series values and ignores `title_series_sorting`.
Its default OPDS template is `{title_sort} - {authors}`. Use `{title} - {authors}` to keep the original title.
See [OPDS download filenames](opds-filename-template.md) for the application rules. The Calibre reference behavior below remains unchanged.

## Sources and tested version

The runtime comparisons use Calibre 9.2.1. The source links below point to Calibre's upstream `master` branch, which can change. Record the installed Calibre version when you repeat the tests.

Source references:

- [Template language manual](https://github.com/kovidgoyal/calibre/blob/master/manual/template_lang.rst).
- [Filename metadata preparation and expansion](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/library/save_to_disk.py): `get_component_metadata()`, `Formatter`, and `get_components()`.
- [Device caller](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/devices/utils.py): `create_upload_path()`.
- [Template engine](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/utils/formatter.py): `TemplateFormatter`.
- [Metadata formatting](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/ebooks/metadata/book/base.py): `format_tags()`, `format_rating()`, and `format_series_index()`.
- [Title and series index rules](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/ebooks/metadata/__init__.py): `title_sort()` and `fmt_sidx()`.
- [Default tweaks](https://github.com/kovidgoyal/calibre/blob/master/resources/default_tweaks.py): `save_template_title_series_sorting` and the language rules for articles.

The tests run Calibre itself with synthetic book metadata. They do not open a library database or change book files. Their expected results describe filename expansion before final path handling.

## The three stages of a Calibre filename

Metadata means the book's fields, such as title and series. Expansion means replacement of template fields with their values. Sanitization means replacement of characters that a filename cannot contain.

Calibre processes filenames in three stages:

1. Prepare the metadata with `get_component_metadata()`.
2. Expand the template with the filename `Formatter`, which extends `TemplateFormatter`.
3. Split and sanitize the path with `get_components()`.

The device caller uses `get_components()` and supplies `opts.send_timefmt`. It then applies device-specific path handling and adds the extension. Save-to-disk uses its own configuration for dates and paths.

A matching parser is not enough. Applications must also agree on metadata preparation and final filename handling. Compare these stages separately so that a filename character replacement does not hide a metadata difference.

## Metadata rules for filenames

### Title and series

The default tweak is `save_template_title_series_sorting = 'library_order'`. Under this configuration, `{title}` uses `mi.title_sort` when that value exists. If that value is absent or empty, Calibre calculates it with `title_sort()`.

For example, `{title}` produces `Book, The` for a book named `The Book` with that stored title sort. With `strictly_alphabetic`, `{title}` produces `The Book`, even when a different title sort exists. This is a filename rule, not a universal meaning of `{title}`.

In Calibre's library database, the stored title sort is `books.sort`. In Calibre's metadata object, it is `mi.title_sort`. Do not assume that a database field and a template field with similar names serve the same purpose.

Calibre calculates `{series}` from the original series name with `title_sort()`. It does not read the database's stored series sort for this step. The same rule applies to custom series fields such as `{#saga}`.

Calibre's article rules depend on language and tweaks. Its `title_sort()` also handles certain surrounding quotation marks. A fixed English regular expression does not reproduce all of these cases.

The template function `raw_field()` can access an original field value instead of the prepared filename value. An implementation that supports this function must retain both versions. A restricted implementation can omit functions, but it must state that limit.

### Numbers, dates, and lists

Calibre uses type-specific conversions. A single generic number-to-string function does not reproduce them. These rules apply before template format codes such as `0>3s`.

| Field or type | Filename preparation in Calibre |
| --- | --- |
| `series_index` | Uses `fmt_sidx()`. Integers have no decimal suffix. Fractions use `%.2f`, then trailing zeros are removed. For example, `2.345` becomes `2.35`. |
| Custom series index | Uses the same `fmt_sidx()` conversion. |
| Standard or custom rating | Divides the stored 0–10 rating by two and preserves float text. A stored `8` becomes `4.0`, not `4`. |
| Custom integer or float | An exact numeric zero becomes empty text. Other values use their string representation. |
| Custom boolean | Uses localized `yes` or `no`. In English, false becomes `no`. |
| `pubdate`, `timestamp`, `last_modified` | Uses the supplied `timefmt`. Undefined dates do not behave like ordinary dates. Test them separately. |
| Custom date | Converts to local time, then uses the supplied `timefmt`. A timezone change can change the date. |
| `authors` | Uses `mi.format_authors()`. The filename field `author` is an alias for this result. |
| `tags` | Uses a locale-aware sort and joins values with `, `. It also removes an initial `/` from the resulting text. |
| List values, including `languages` | The filename formatter joins remaining list values with `,`, without a following space. |
| `identifiers` | Uses Calibre's formatted identifiers value. This field is separate from an individual ISBN. |
| `id` | Uses the `book_id` supplied to the filename preparation function. |

A custom lookup name starts with `#`. For a custom series named `#saga`, its number is available as `#saga_index`. A composite field is a custom field calculated from a template. The filename formatter evaluates its template against the prepared filename values and detects recursive references.

There are two different date defaults in the source. The helper function's default argument is `timefmt='%b %Y'`. The save and send configuration defaults are both `'%b, %Y'`, with a comma.

For May 6, 2020, the helper default produces `May 2020`. A device caller using the default send configuration produces `May, 2020`. A test that calls the helper without a date format does not reproduce the device caller's default.

### Field lookup and whitespace

The filename formatter converts field names to lowercase. An unknown field normally becomes empty text. A parser that rejects every unknown field implements a stricter subset, not the same lookup behavior.

Before string indexing, the filename formatter replaces `/` and `\` inside metadata values with underscores. Literal `/` characters in the template remain path separators for `get_components()`. This distinction keeps a slash inside a title from creating a directory.

The template engine normally compresses repeated whitespace and strips surrounding spaces after expansion. String padding with spaces can therefore disappear. Zero padding remains visible.

For example, `x{series_index:>5s}x` with index `2` produces `x 2x`, not `x    2x`. Applications that use Python's string formatter without this final step produce different text. The test script below includes this case.

## Template syntax and supported subsets

A field expression uses `{lookup_name}`. The basic extended form is `{lookup_name:format|prefix|suffix}`. Missing field values suppress both the prefix and the suffix.

Examples from the supported Calibre syntax:

| Template | Meaning |
| --- | --- |
| `{title}` | Insert the prepared title value. |
| `{author_sort[0]}` | Select the first character of the author sort string. |
| `{series_index:0>3s}` | Pad a nonempty series index string to three characters with zeros. |
| `{title:.20s}` | Keep at most 20 characters of the prepared title. |
| `{series:\|\| - }{title}` | Add ` - ` after the series only when the series exists. |
| `{series}{series_index:\| - \| - }{title}` | Add separators around the series index only when it exists. |
| `{title:uppercase()}` | Apply a template function. |
| `{{title}}` | Produce literal braces around `title`. |

Calibre supports string and numeric format codes. A numeric code must accept the prepared field value. For example, `{series_index:03d}` works for index `2`, but fails for the prepared string `2.35`.

Calibre also supports Single Function Mode, Template Program Mode, General Program Mode, and Python Template Mode. A filename implementation does not need to support all of them. Consult the manual before implementing their evaluation rules.

Do not execute untrusted template text with Python `eval()` or `exec()`. This can execute arbitrary code. A restricted parser can support substitutions, format codes, and conditional prefixes without implementing program modes.

If an application implements a subset, define its accepted syntax explicitly. Accepted templates must retain Calibre's output rules if matching filenames is the goal. Unsupported syntax and different output for accepted syntax are separate compatibility problems.

Useful standard fields include the following names. This is a starting set, not a complete list of Calibre fields:

```text
author author_sort authors id identifiers isbn languages last_modified
pubdate publisher rating series series_index tags timestamp title title_sort
```

Custom fields require their `#lookup_name`, not their displayed column heading. Composite fields also require a compatible evaluator for their source templates. If an implementation cannot evaluate a composite, report that limit instead of silently claiming full support.

## Examples of incompatible output

The following cases use English, UTC, and `library_order`. The alternative column records outputs that look reasonable but do not match Calibre. The date rows specify which date format they use.

| Input and template | Calibre | Incompatible alternative |
| --- | --- | --- |
| Title `The Book`, sort `Book, The`: `{title}` | `Book, The` | `The Book` |
| Custom series `The Cycle`: `{#saga}` | `Cycle, The` | `The Cycle` |
| Index `2.345`: `{series_index}` | `2.35` | `2.345` |
| Stored rating `8`: `{rating}` | `4.0` | `4` |
| Custom integer `0`: `{#count:0>3s}` | Empty | `000` |
| Custom boolean false: `{#read}` | `no` | `No` |
| Languages `eng`, `fra`: `{languages}` | `eng,fra` | `eng, fra` |
| Tags supplied as `Space`, `Fiction`: `{tags}` | `Fiction, Space` | `Space, Fiction` |
| May 6, 2020, helper date format: `{pubdate}` | `May 2020` | `2020-05-06` |
| May 6, 2020, default send date format: `{pubdate}` | `May, 2020` | `2020-05-06` |
| Custom date with default send date format: `{#date}` | `May, 2020` | `2020-05-06` |
| Custom list `Favorites`, `Other`: `{#shelf}` | `Favorites,Other` | `Favorites, Other` |
| Index `2`: `x{series_index:>5s}x` | `x 2x` | `x    2x` |

Matching `{title}` alone does not establish compatibility. Custom series sorting, number conversion, and list formatting can change the resulting filename. Those differences can prevent applications from matching files by name across devices.

## Reproduce the Calibre reference tests

The reference script below runs Calibre itself. It creates synthetic metadata and asserts Calibre's expected results. It prints JSON Lines, which means one JSON object per line.

Prerequisites are an installed Calibre with `calibre-debug` and a text editor. Save the following Python block as `/tmp/calibre_filename_probe.py`. No application checkout or additional Python package is required.

```python
import json
from datetime import datetime, timezone

from calibre.constants import __version__
from calibre.ebooks.metadata.book.base import Metadata
from calibre.library.save_to_disk import Formatter, config, get_component_metadata
from calibre.utils.config_base import tweaks

# Use an isolated Calibre configuration directory when running this script.
tweaks['save_template_title_series_sorting'] = 'library_order'
tweaks['default_language_for_title_sort'] = 'eng'
DATE = datetime(2020, 5, 6, 12, tzinfo=timezone.utc)
SEND_TIMEFMT = '%b, %Y'

# label, type, value, series index, multiple values
CUSTOM = [
    ('saga', 'series', 'The Cycle', 2.345, False),
    ('count', 'int', 0, None, False),
    ('read', 'bool', False, None, False),
    ('date', 'datetime', DATE, None, False),
    ('stars', 'rating', 8, None, False),
    ('shelf', 'text', ['Favorites', 'Other'], None, True),
]


def metadata(index=2.345, has_series=True):
    mi = Metadata('The Book', ['Ann Writer', 'Ben Reader'])
    mi.title_sort = 'Book, The'
    mi.author_sort = 'Writer, Ann & Reader, Ben'
    mi.series = 'The Saga' if has_series else None
    mi.series_index = index if has_series else None
    mi.rating = 8
    mi.languages = ['eng', 'fra']
    mi.tags = ['Space', 'Fiction']
    mi.pubdate = DATE
    for label, kind, value, extra, multiple in CUSTOM:
        mi.set_user_metadata('#' + label, {
            'datatype': kind, 'name': label, 'label': label, 'display': {},
            'is_multiple': {'list_to_ui': ', '} if multiple else {},
            '#value#': value, '#extra#': extra,
        })
    return mi


def calibre_text(template, mi, timefmt):
    values = get_component_metadata(template, mi, 42, timefmt=timefmt)
    # unsafe_format propagates errors. It is not a sandbox for untrusted text.
    return Formatter().unsafe_format(template, values, mi)


# name, template, expected Calibre text, index, series present, time format
CASES = [
    ('title', '{title}', 'Book, The', 2.345, True, SEND_TIMEFMT),
    ('series', '{series}', 'Saga, The', 2.345, True, SEND_TIMEFMT),
    ('custom_series', '{#saga}', 'Cycle, The', 2.345, True, SEND_TIMEFMT),
    ('fraction', '{series_index}', '2.35', 2.345, True, SEND_TIMEFMT),
    ('custom_fraction', '{#saga_index}', '2.35', 2.345, True, SEND_TIMEFMT),
    ('rating', '{rating}', '4.0', 2.345, True, SEND_TIMEFMT),
    ('custom_rating', '{#stars}', '4.0', 2.345, True, SEND_TIMEFMT),
    ('numeric_zero', '{#count:0>3s}', '', 2.345, True, SEND_TIMEFMT),
    ('boolean', '{#read}', 'no', 2.345, True, SEND_TIMEFMT),
    ('languages', '{languages}', 'eng,fra', 2.345, True, SEND_TIMEFMT),
    ('tags', '{tags}', 'Fiction, Space', 2.345, True, SEND_TIMEFMT),
    ('helper_date', '{pubdate}', 'May 2020', 2.345, True, '%b %Y'),
    ('send_date', '{pubdate}', 'May, 2020', 2.345, True, SEND_TIMEFMT),
    ('custom_date', '{#date}', 'May, 2020', 2.345, True, SEND_TIMEFMT),
    ('custom_list', '{#shelf}', 'Favorites,Other', 2.345, True, SEND_TIMEFMT),
    ('space_padding', 'x{series_index:>5s}x', 'x 2x', 2, True, SEND_TIMEFMT),
    ('zero_padding', '{series_index:0>3s}', '002', 2, True, SEND_TIMEFMT),
    ('numeric_format', '{series_index:03d}', '002', 2, True, SEND_TIMEFMT),
    ('first_character', '{author_sort[0]}', 'W', 2, True, SEND_TIMEFMT),
    ('conditional', '{series:|| - }{title}', 'Saga, The - Book, The',
     2, True, SEND_TIMEFMT),
    ('missing_series', '{series}{series_index:| - | - }{title}', 'Book, The',
     2, False, SEND_TIMEFMT),
    ('case_insensitive', '{TITLE}', 'Book, The', 2, True, SEND_TIMEFMT),
    ('unknown', 'x{not_a_field}x', 'xx', 2, True, SEND_TIMEFMT),
    ('literal_braces', '{{title}}', '{title}', 2, True, SEND_TIMEFMT),
]

opts = config().parse()
print(json.dumps({
    'kind': 'environment', 'calibre_version': __version__,
    'configured_timefmt': opts.timefmt,
    'configured_send_timefmt': opts.send_timefmt,
    'locale': 'en', 'timezone': 'UTC',
}))


def run_case(name, template, expected, index, has_series, timefmt):
    actual = calibre_text(template, metadata(index, has_series), timefmt)
    assert actual == expected, (name, expected, actual)
    print(json.dumps({
        'kind': 'case', 'case': name, 'template': template,
        'sorting': tweaks['save_template_title_series_sorting'],
        'series_index': index, 'has_series': has_series,
        'timefmt': timefmt, 'expected': actual,
    }, ensure_ascii=False))


for case in CASES:
    run_case(*case)

# This tweak changes all three fields, not only the standard title.
tweaks['save_template_title_series_sorting'] = 'strictly_alphabetic'
for name, template, expected in [
    ('alphabetic_title', '{title}', 'The Book'),
    ('alphabetic_series', '{series}', 'The Saga'),
    ('alphabetic_custom_series', '{#saga}', 'The Cycle'),
]:
    run_case(name, template, expected, 2.345, True, SEND_TIMEFMT)

print(json.dumps({'kind': 'summary', 'passed': len(CASES) + 3}))
```

Run the script with an isolated Calibre configuration:

```sh
CALIBRE_CONFIG_DIRECTORY="$(mktemp -d)" \
CALIBRE_OVERRIDE_LANG=en TZ=UTC LC_ALL=C \
calibre-debug -e /tmp/calibre_filename_probe.py > /tmp/calibre-filename-results.jsonl
```

The command uses a new temporary configuration directory. It does not change your normal Calibre configuration. The environment fixes the language and timezone so that date and boolean results are repeatable.

A successful run exits with status zero. Its final record is `{"kind": "summary", "passed": 27}`. This result covers 24 baseline cases and three cases for the alternative sorting tweak.

The JSON records preserve spaces inside expected values. Do not trim those strings before comparing them. The `metadata()` function and `CUSTOM` list define the shared book data for every case.

## Test another implementation

Use the same metadata to compare an independent implementation with the reference results. A case record supplies the template, sorting rule, series presence, series index, and date format. The `expected` field contains Calibre's expanded text.

For each supported case, follow these steps:

1. Construct the book metadata from `metadata()` and `CUSTOM`.
2. Apply the case's series presence and series index.
3. Select the case's sorting rule and date format.
4. Expand the template with the implementation under test.
5. Compare its exact text with the case's `expected` value.

If a case uses unsupported syntax, report it as unsupported. Do not count it as a passing compatibility test. Keep rejected syntax separate from accepted syntax that produces different text.

These tests cover metadata preparation and expansion only. They do not test database queries, HTTP responses, filename sanitization, or device behavior. Add application-specific tests for those parts.

For a restricted implementation, document its supported fields and format codes beside its tests. Include missing fields, empty strings, and custom numeric zero. Those values are not interchangeable in Calibre.

## Final filenames and OPDS downloads

OPDS is a catalog format used by reading applications. For an OPDS download, the HTTP `Content-Disposition` header suggests a filename. The client decides whether to use that filename and where to store the file.

Calibre templates can describe directory paths. An OPDS filename cannot reliably request those directories. [HTTP guidance](https://www.rfc-editor.org/rfc/rfc6266.html#section-4.3) tells clients to discard directory components.

For a single-filename service, define directory handling explicitly. Reject path templates or apply a documented single-filename policy. Do not silently claim that replacing every slash reproduces a Calibre directory template.

The service must add the extension for the file that it actually sends. Do not require users to add the extension to a metadata template. Test format fallback and device-specific extensions separately from title expansion.

Define the following service policies independently of Calibre's template rules:

- Filename sanitization for separators, control characters, and reserved device names.
- Length limits for templates, formatting widths, and final filenames.
- Unicode handling, including optional transliteration and byte-safe truncation.
- Empty-result fallback, such as `book-ID` before the extension.
- Invalid-template handling that does not prevent an authorized download.
- Collision handling when different books produce the same name.

For example, an implementation can limit templates to 1024 characters and basenames to 128 UTF-8 bytes. Those are application policies, not universal Calibre limits. A character limit and a byte limit differ for non-ASCII text.

A blank preference can preserve an application's old naming behavior. State that behavior explicitly, such as `Title - First Author.ext` with the original title. Do not confuse that fallback with expansion of a configured `{title}` template.

In KOReader, enable Use server filenames in the OPDS catalog configuration. Without that preference, KOReader normally constructs `Author - Title.epub` from the feed. Other clients can also ignore the suggested name.

A changed template affects future downloads, not files already on a device. It does not require changes to library metadata or stored library filenames. Include `{id}` when different books otherwise produce the same filename.

## Implementation requirements and remaining coverage

A compatibility profile is an explicit set of matching rules. Start with a profile that names the Calibre version, sorting tweak, date format, locale, timezone, and supported syntax. Keep those values visible in test output.

Use the following requirements for a filename implementation:

1. Preserve original metadata separately from prepared filename values.
2. Apply title and series sorting according to the filename tweak.
3. Apply each numeric, date, boolean, and list conversion by field type.
4. Apply the same preparation to custom fields and supported composite templates.
5. Expand accepted syntax with Calibre's empty-value and whitespace rules.
6. Reject unsupported syntax explicitly instead of pretending to support the full language.
7. Apply the destination's filename safety rules after expansion.
8. Add the actual file extension outside the metadata template.
9. Compare results against the installed Calibre engine in automated tests.

The current probe is a baseline, not complete coverage. Extend it with missing and undefined values, rating zero, quoted titles, non-English articles, and dates near timezone boundaries. Add composites, custom floats, identifiers, and locale-sensitive tag order if the implementation supports them.

Test final filenames separately from expansion. Include slash characters in metadata, literal directory templates, reserved device names, Unicode, byte limits, empty results, and format fallback. Server output alone does not establish device behavior.
