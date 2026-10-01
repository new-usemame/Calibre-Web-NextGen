# Tag books by ingest folder

In New UI **Admin → Ingest folder labels**, or the Classic **NextGen settings** page, choose **Tags** or an existing custom column. This feature starts disabled. Custom columns must use Calibre’s comma-separated text, like tags, type; create a column in Calibre desktop first. CWNG lists compatible columns and validates the selection.

Dropping a book into `ingest/James/` adds the `James` value without removing existing tags or custom-column values. A file directly under the ingest root adds no value. `ingest/James/scifi/book.epub` adds only `James` by default; **Include nested folders** adds both `James` and `scifi`. Labels come from the original ingest path, so conversion temporary directories do not become labels.

This can support per-user tag or custom-column view restrictions, category folders and download clients that publish into per-user folders. It writes metadata; it does not grant account roles, create shelves or change the existing access rules. Configure view restrictions separately.

If a configured custom column is removed or becomes incompatible, choose another target or disable the feature before importing again. An invalid target must be fixed rather than silently importing without the expected label. Existing imported books are not retrospectively tagged.

Explicit acquisition sources outside the configured ingest root receive no folder labels. A path inside the ingest root that resolves through a symlink outside that root is rejected, with the original retained.
