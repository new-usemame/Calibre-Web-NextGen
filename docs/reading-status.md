# Reading status

Each account can choose **Unread**, **Finished**, **Currently reading**,
**Did not finish**, or **On hold** on a book's detail page in either the New UI
or Classic. Did not finish is for books you decided to leave unfinished; On
hold is for books you intend to return to later.

Choosing Did not finish or On hold preserves your reading positions on every
device, bookmarks, highlights, notes, reading history, and start counts. The
choice applies to your account. It does not pause another reader's copy of the
same book.

The Library filters, Advanced Search, Classic sidebar, and magic-shelf read
status rules can select these states. The existing Unread and Yet to Read
views continue to include books currently being read, but exclude Did not
finish and On hold. OPDS has separate Did not finish and On hold feeds.

Background browser, Kobo, and KOReader progress updates may advance your saved
position while a book remains paused. They do not automatically resume or
finish it. Choose a new status, open the ordinary browser reader, or explicitly
choose a read status in KOReader to resume. Opening in lookup mode preserves
the current status and positions. Marking a book Unread retains its existing
reset behavior and clears stored reading positions.

When duplicate books are merged and either copy is paused, your later explicit
reading-status choice wins. Background activity on the other copy does not
count as a choice to resume. Reading positions and start counts are preserved
by the merge.

A deliberate status choice is recorded separately from device activity. During
book-duplicate merging, a newer explicit pause or resume wins even if an older
copy received more recent automatic progress. The upgrade adds one nullable
choice timestamp per reading-state row and copies existing status timestamps
once as a historical baseline; older data cannot identify past explicit choices
more precisely. Future automatic reports do not advance the choice timestamp.

## Calibre custom read column

If an administrator links Read/Unread status to a Calibre boolean custom column,
Did not finish and On hold are stored as personal states in Calibre-Web-NextGen.
Choosing either state leaves the shared Calibre column unchanged. Your paused
choice takes precedence in your Calibre-Web-NextGen views; other accounts
continue to see the shared column's value. Calibre desktop therefore retains
the previous shared read value, which may be read or unread. A boolean column
cannot represent or round-trip either paused state.

Explicit Finished and Unread choices continue to update the shared read column.
Explicitly resuming a paused book as Currently reading, including opening the
ordinary browser reader, clears the shared finished marker so Currently
reading can be shown. Background progress on a paused book leaves the column
alone.

## Device and API compatibility

Kobo's reading-state protocol receives Did not finish and On hold as
`ReadyToRead`. KOReader's existing three-state library protocol receives them
as `unread`. These are compatibility projections: your saved positions and
the server's personal paused state remain intact. An automatic Kobo status
report cannot clear the personal paused choice.

The book list and detail API expose an additive `read_status` field with one
of `unread`, `finished`, `in_progress`, `did_not_finish`, or `on_hold`. Existing
`read` and `in_progress` booleans retain their meanings and are both false for
the two paused states.

Set an explicit state with:

```http
POST /api/v1/books/42/read-status
Content-Type: application/json

{"status": "on_hold"}
```

A successful response is `{"status": "on_hold"}`. Only books the caller can
access and that belong to their library accept a status change. Unknown status
values return HTTP 400; inaccessible books return HTTP 404; accessible books
outside the caller's library return HTTP 403. The existing boolean read API
and Classic read/unread toggle remain available.
