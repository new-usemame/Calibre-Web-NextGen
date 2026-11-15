# Names for emailed book attachments

An administrator can set **eReader attachment filename template** in the New UI's
**Admin → Email server** form or Classic's **Edit Email Server Settings** page.
It controls the filenames attached to books sent to configured reader email addresses,
including Kindle addresses, by manual sends, automatic sends and conversion-to-send.

Leave it blank to keep the book's existing Calibre filename. The setting applies to
book attachments; account messages and test emails keep their existing behavior.

For example, with title `The Book`, series `The Saga` and series index `2.5`:

| Template | Emailed EPUB filename |
| --- | --- |
| `{series} #{series_index} - {title}` | `The Saga #2.5 - The Book.epub` |
| `({series}) {series_index} - {title}` | `(The Saga) 2.5 - The Book.epub` |
| `{authors} - {title}` | `Ann Writer - The Book.epub` |
| `{series:|(|) }{series_index:|#| - }{title}` | `(The Saga) #2.5 - The Book.epub` |

The last example omits the series decorations when a book has no series. For a
plain title, use `{title}`. Titles and series use their ordinary metadata text;
series numbers retain fractions. The actual format's extension is added automatically.

The bounded metadata grammar also supports the fields and conditional prefix/suffix
syntax described in [OPDS download filenames](opds-filename-template.md), including
eligible custom columns. The OPDS setting stays independent and continues to use its
sorted title and series names. Templates are limited to 1,024 characters and support
string padding, rather than executable Calibre programs or Python expressions.
Invalid templates are rejected before other mail settings are saved.

Attachment basenames use the existing filename sanitization and transliteration
preference, with a 128-byte basename limit and safe handling of path separators,
control characters and reserved names. The attachment name is separate from the
library path: sending a book preserves the original library file. Personal-cover and
embedded-metadata delivery still select the bytes to send, and filename-based KOReader
matching registers the final attachment name.

This setting changes the email attachment name. A reader or delivery service may
choose its displayed title from embedded metadata or its own processing; the server
does not control that display choice.
