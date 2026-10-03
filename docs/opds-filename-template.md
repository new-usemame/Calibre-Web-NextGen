# OPDS download filenames

The OPDS filename template controls the name that the server suggests for each download.
The server sends this name in `Content-Disposition`, the HTTP header that suggests a download filename.
The device decides whether to use that name.
This preference does not rename library files, change book metadata, or change filenames for web downloads or native Kobo sync.

## Set the template

The preference applies to all users and all book formats downloaded through OPDS.
In the React interface, the preference is under Admin → Library settings.
In the Classic interface, it is under Admin → UI Configuration → OPDS Downloads.

1. Open the configuration page for your interface.
2. Enter a template in OPDS download filename template.
3. Select Save settings in React or Save in Classic.

For example, enter `{series} - {series_index:0>3s} - {title}`.
For book 2 in “The Saga,” titled “The Book,” this produces `The Saga - 002 - The Book.epub`.
Do not include the extension in the template.
The server adds the extension for the format it sends, such as `.epub` or `.pdf`.

If you want the original naming behavior, clear the preference and save.
A blank preference keeps `Title - First Author.ext`, with the original title and only the first author.
The default template is `{title_sort} - {authors}`, such as `Book, The - Ann Writer.epub`.
Use `{title} - {authors}` for `The Book - Ann Writer.epub` instead.
New installations and upgrades that add this preference use the default template.
An upgrade preserves an existing saved template, including a blank value.

## Fields and formatting

A field is a metadata name inside braces, such as `{title}`.
Missing metadata becomes empty text, even when the field has padding or character indexing.
A missing series in `{series} - {title}` leaves the leading separator.
Repeated whitespace becomes one space, and the server removes outer spaces after expansion.
Field names are case-insensitive. Use `{{` and `}}` for literal braces.

| Field | Value |
| --- | --- |
| `{author}` | An alias for `{authors}`. |
| `{author_sort}` | The author sort string, such as `Writer, Ann`. |
| `{authors}` | All authors, separated by ` & `. An ampersand within a name becomes `&&`, as in Calibre. |
| `{id}` | The internal Calibre book ID. |
| `{identifiers}` | Identifier pairs, such as `doi:example, isbn:123`. |
| `{isbn}` | The ISBN. |
| `{languages}` | Language codes, separated by commas without spaces, such as `eng,fra`. |
| `{last_modified}` | The date when the book metadata last changed. |
| `{pubdate}` | The publication date. |
| `{publisher}` | The publisher. |
| `{rating}` | The rating from 0 to 5 stars, such as `4.0` or `4.5`. An absent rating is empty. A stored zero becomes `0.0`. |
| `{series}` | The original series name. |
| `{series_sort}` | The stored series sort value, such as `Saga, The`, without a calculated fallback. |
| `{series_index}` | The series number. Integers have no decimal suffix. Fractions use Calibre's two-decimal conversion. No series means no number. |
| `{tags}` | Sorted tags, separated by a comma and a space. |
| `{timestamp}` | The date when the book entered the library. |
| `{title}` | The original title, such as `The Book`. |
| `{title_sort}` | The stored title sort value, without a calculated fallback. |
| `{#lookup_name}` | A custom field, identified by its Calibre lookup name. |

Dates use Calibre's default save/send format, `%b, %Y`, such as `May, 2020`.
Month names follow the server's time locale. An unset standard Calibre date is empty.
Custom dates use the server's local timezone. A custom date without a timezone is treated as UTC first.

`{title}` always uses the original title. `{title_sort}` uses the stored title sort value, such as `Book, The`.
If the stored sort value is absent, `{title_sort}` becomes empty text.
Standard and custom series keep their original names.
Use `{series_sort}` for the standard series sort value. If the book has no series or stored sort value, this field becomes empty text.
For example, `{series_sort} - {title_sort}` produces `Saga, The - Book, The`.
The server does not apply article sorting to these fields.

Character indexing starts at zero. `{author_sort[0]}` selects the first character, not the first author.
An index outside the text produces empty text.
String formatting supports alignment, padding, and a character limit:

- `{series_index:0>3s}` produces `002` for series number 2.
- `x{series_index:>3s}x` produces `x 2x`. Calibre's whitespace rule compresses the padding.
- `{title:.20s}` keeps the first 20 characters of the title.

Custom fields support text, numbers, dates, yes/no values, ratings, and custom series.
Multiple values use commas without spaces. Numeric zero becomes empty text, even with padding.
A text field containing `0` remains `0`. False becomes `no`, and true becomes `yes`.
Custom ratings use the same conversion as standard ratings.
For a custom series named `#saga`, `{#saga_index}` supplies its number.
Missing, deleted, or unavailable custom fields produce empty text.

This is a limited Calibre-style template language, not the complete Calibre template engine.
It does not support template functions, conditional prefixes and suffixes, numeric format codes, or Calibre program mode.
A computed custom field works when its source template uses the supported syntax.
Otherwise, that field produces empty text and the server logs a warning.
Templates cannot access Python attributes or execute code.

## Compatibility profile

OPDS uses Calibre's default `%b, %Y` date format, but does not apply its filename sorting tweak.
It does not import preferences from a desktop Calibre installation.
The renderer accepts `title_series_sorting` and `title_regex` for existing callers but ignores both arguments.
It accepts an explicit `timefmt` argument. The admin editors do not expose that option.

Calibre's default `library_order` replaces title and series values with sort forms for filenames.
CWNG keeps their original values, which match Calibre's `strictly_alphabetic` profile.
Use `{title_sort}` explicitly when you want the stored sort value in either application.
Tags use the application's Unicode sort key, not Calibre's locale-specific ICU sorting library.
Boolean text stays in English, independent of the requesting user's language.
Different sorting tweaks, tag ordering, or date preferences can therefore produce different filenames.

The parser still rejects unknown standard fields and unsupported syntax.
Out-of-range character indexes produce empty text, and recursive composites stop at the safety limit.
These are intentional limits, not full Calibre engine behavior.

## Filename limits and device behavior

The template can contain up to 1024 characters. Formatting widths and character limits cannot exceed 128.
The final name, without its extension, is limited to 128 UTF-8 bytes without splitting a character.
The existing filename transliteration preference still applies.
If the template produces no usable name, the server uses `book-ID`, such as `book-42.epub`.
If a stored template is invalid, downloads fall back to the original naming behavior.

Do not use `/` or `\` to request subfolders.
Content-Disposition supplies a filename, not a destination path.
[HTTP guidance](https://www.rfc-editor.org/rfc/rfc6266.html#section-4.3) tells clients to discard directory components.
CWNG replaces both separators, control characters, and unsafe filename characters with underscores.
The device controls the download folder.

In KOReader, enable Use server filenames in the OPDS catalog configuration.
Without this preference, KOReader normally constructs `Author - Title.epub` from the feed and ignores the suggested server filename.
Other clients can also ignore Content-Disposition.
This preference does not rename files that are already on the device.
Include `{id}` if different books can otherwise produce the same filename.

## Stored configuration

The preference is `config_opds_filename_template` in the `settings` table of `app.db`.
Both admin editors use the same syntax validation.
The admin API reads and writes this preference through `/api/v1/admin/config`.
Saving takes effect on the next OPDS download without a restart.

## Comparison tests

`tests/fixtures/calibre_filename_templates.json` contains expected expansions generated by Calibre 9.2.1 with `strictly_alphabetic` sorting.
Separate unit tests confirm that CWNG ignores the sorting arguments and reads `{series_sort}` from the library database.
The unit tests read this file and use real SQLite tables for custom fields.
They do not require Calibre to be installed.

To compare the fixture with an installed Calibre, run this command from the repository root:

```sh
CALIBRE_CONFIG_DIRECTORY="$(mktemp -d)" CALIBRE_OVERRIDE_LANG=en LC_ALL=C TZ=UTC \
calibre-debug -e scripts/generate_calibre_filename_fixtures.py -- --check
```

To regenerate the fixture, omit `-- --check`.
Review the generated changes before accepting results from a different Calibre version.
The generator does not import the application renderer.

To run the application tests in an environment with the project dependencies and pytest, use:

```sh
python -m pytest tests/unit/test_opds_filename_template.py -q
```
