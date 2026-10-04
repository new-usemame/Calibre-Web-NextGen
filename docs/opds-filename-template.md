# OPDS download filenames

The OPDS filename template controls the name that the server suggests for each download.
The server sends this name in `Content-Disposition`, the HTTP header that suggests a download filename.
The device decides whether to use that name.
This preference does not rename library files, change book metadata, or change filenames for web downloads or native Kobo sync.

## Set the template

The preference applies to all users and all book formats downloaded through OPDS.
A template can use custom fields hidden from the book pages; their values become part of the filename for every user who can download the book. Choose fields suitable for that audience.
In the React interface, the preference is under Admin → Library settings.
In the Classic interface, it is under Admin → UI Configuration → OPDS Downloads.

1. Open the configuration page for your interface.
2. Enter a template in OPDS download filename template.
3. Select Save settings in React or Save in Classic.

For example, enter `{series:|| - }{series_index:0>3s|| - }{title}`.
For book 2 in “The Saga,” titled “The Book,” this produces `Saga, The - 002 - Book, The.epub`.
Do not include the extension in the template.
The server adds the extension for the format it sends, such as `.epub` or `.pdf`.

If you want the original naming behavior, clear the preference and save.
A blank or whitespace-only preference keeps `Title - First Author.ext`, with the original title and only the first author.
Existing installations start with a blank preference.

## Fields and formatting

A field is a metadata name inside braces, such as `{title}`.
Missing metadata becomes empty text, even when the field has padding or character indexing.
Literal text remains unchanged, so a missing series in `{series} - {title}` leaves the leading separator.
Use `{{` and `}}` for literal braces.

| Field | Value |
| --- | --- |
| `{author_sort}` | The author sort string, such as `Writer, Ann`. |
| `{authors}` | All authors in Calibre’s stored author order, separated by ` & `. |
| `{id}` | The internal Calibre book ID. |
| `{isbn}` | The ISBN. |
| `{languages}` | Language codes, separated by commas, such as `eng, fra`. |
| `{last_modified}` | The date when the book metadata last changed. |
| `{pubdate}` | The publication date. |
| `{publisher}` | The publisher. |
| `{rating}` | The rating from 0 to 5 stars. An unset rating is empty. |
| `{series}` | The series sort name. |
| `{series_index}` | The series number, without unnecessary decimal zeros. No series means no number. |
| `{tags}` | Tags, separated by commas. |
| `{timestamp}` | The date when the book entered the library. |
| `{title}` | The title sort name. |
| `{#lookup_name}` | A custom field, identified by its Calibre lookup name. |

Dates use `YYYY-MM-DD`. An unset Calibre date is empty.
Title and series use their stored sort names, with leading articles at the end.
If a stored sort name is absent, the server applies the configured title-sort rule.
For example, “The Book” becomes “Book, The.”
The original blank-preference behavior does not apply this sorting to filenames.

Character indexing starts at zero. `{author_sort[0]}` selects the first character, not the first author.
An index outside the text produces empty text.
String formatting supports alignment, padding, and a character limit:

- `{series_index:0>3s}` produces `002` for series number 2.
- `{series_index:>3s}` produces two spaces followed by `2`.
- `{title:.20s}` keeps the first 20 characters of the title.

Custom fields support text, numbers, dates, yes/no values, ratings, and custom series.
Multiple values keep their stored link order and use commas; custom name fields use ` & `. Zero remains `0`, and false remains `No`.
For a custom series named `#saga`, `{#saga_index}` supplies its number.
Missing, deleted, or unavailable custom fields produce empty text.

Conditional text uses `{field:format|prefix|suffix}`. The prefix and suffix appear only when the field has a value.
For example, `{series:|| - }{series_index:0>3s|| - }{title}` also produces `Book, The.epub` for a standalone book.
Both separators are required; affixes cannot contain nested fields. Use explicit alignment for zero padding:
`0>3s` pads a series number on the left, while ambiguous `03` and `05s` are rejected.

This is a limited [Calibre-style template language](https://manual.calibre-ebook.com/template_lang.html), not the complete Calibre template engine.
It does not support template functions, numeric format codes, or Calibre program mode.
A computed custom field works when its source template uses the supported syntax; title and series inside that computed value use their original metadata names.
Composite expansion is limited to ten nested lookups and 256 custom-field evaluations per download. Repeated composites reuse results only within the same depth and cycle context. Cycles and deeper references become empty text, without changing the result of a later shallow reference.
Otherwise, that field produces empty text and the server logs a warning.
Templates cannot access Python attributes or execute code.

## Filename limits and device behavior

The template can contain up to 1024 characters. Formatting widths and character limits cannot exceed 128.
The final name, without its extension, is limited to 128 UTF-8 bytes without splitting a character.
The existing filename transliteration preference still applies.
If the template produces no usable name, the server uses `book-ID`, such as `book-42.epub`.
If a stored template is invalid, downloads fall back to the original naming behavior.

Do not use `/` or `\` to request subfolders.
Content-Disposition supplies a filename, not a destination path.
[HTTP guidance](https://www.rfc-editor.org/rfc/rfc6266.html#section-4.3) tells clients to discard directory components.
CWNG removes control, directional and other invisible formatting, and Unicode line-separator characters. It preserves the zero-width joiner and non-joiner used in scripts and emoji. It replaces path separators and unsafe filename characters with underscores.
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
