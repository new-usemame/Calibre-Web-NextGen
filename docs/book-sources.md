# Book sources (Beta)

Find books connects an administrator's catalogs to CWNG's library. Accounts with access can browse a connected catalog, choose an available EPUB or PDF, and request its import. The file goes through CWNG's existing ingest and configured processing before appearing in the Global Library. Accounts using My Library can also add the resulting book to their own selection.

This feature is off by default. Existing accounts and library selections do not gain acquisition permissions automatically.

Both screens live in the New UI only. The classic interface has no book-sources
panel and no **Find books** entry, so an administrator working in classic sees
the feature exactly as an account without access does: not at all. That is
deliberate rather than unfinished. The feature talks to the `/api/v1`
acquisition routes and gates its navigation on the `acquisition_access` flag
from `/me`, neither of which the classic templates consume; a classic twin
would mean a second set of routes handling remote fetches, stored credentials
and ingest, which is the part of this feature least worth duplicating. It
follows the same pattern as the New UI's device administration.

## Connect a catalog

1. Open **Administration → Book sources**.
2. Switch on **Allow requests from book sources**. Nothing is fetched and no account gains a permission until you do.
3. Add a catalog with a name and an **OPDS catalog address**. Select no sign-in, username and password, or a token, as the catalog requires.
4. Choose **Test connection**, then mark the catalog **Available to users**. A successful test confirms only that CWNG can read the catalog; it does not download a book.
5. Under **Who can use it**, grant the intended accounts **Can browse and request**. Add **No approval needed** only for accounts that should bypass approval; everyone else's requests appear under **Waiting for approval** for an administrator to approve.
6. The granted account opens **Find books**, selects the catalog, and browses or searches when the catalog advertises search. They choose a format, and the book is requested or imported according to their permission.

New connections start disabled. The setup page reports runtime problems such as an unavailable ingest service, a missing connection key, or no supported file formats. Correct those before requesting imports. This Beta requires CWNG's container ingest service and existing background scheduler.

The public Project Gutenberg OPDS entrypoint is `https://www.gutenberg.org/ebooks/search.opds/`. Gutenberg publishes its supported machine-readable entrypoints in its [catalog documentation](https://www.gutenberg.org/ebooks/offline_catalogs.html). Use a provider's documented catalog endpoint rather than its HTML search page.

## What is supported

| Capability | Current support |
| --- | --- |
| Catalogs | OPDS 1 Atom and OPDS 2 JSON |
| Browsing | Catalog navigation, groups, facets and pagination |
| Search | Advertised OpenSearch descriptions and supported OPDS 2 keyword templates |
| Files | Direct EPUB and PDF acquisition links, intersected with the instance's allowed upload formats |
| Authentication | None, HTTP Basic, or Bearer credentials scoped to configured origins |
| Local services | Explicit administrator configuration of private origins and network ranges |
| Import | Existing CWNG ingest, configured repair/conversion, durable import receipt and account attribution |

A catalog may list books without a supported direct file. Purchase, borrowing, DRM, previews, HTML landing pages and indirect acquisition flows are not presented as downloadable files. Inline catalog artwork is optional and is not downloaded.

Anna's Archive, Newznab/Torznab indexers, Usenet download clients and torrent clients are not implemented by this Beta. They are not shown as working providers. The framework separates catalog discovery, file transport and library ingestion so additional protocol adapters can reuse the same permission, request and import boundaries.

## Requests and existing books

Requests belong to the account that created them. Repeating the same submission after an uncertain response returns the original request. Accounts cannot use another account's catalog selections or inspect its requests.

Imported means that CWNG has recorded the actual library book IDs and completed its import receipt. A download finishing alone does not mean the book is available. The book links still follow normal library visibility rules.

Acquisition preserves an existing same-format edition when Calibre matches its title and author. It does not overwrite that edition, even when ordinary ingest is configured to overwrite duplicates. Its result identifies that the existing edition was retained. A different format may create a separate library record. Existing highlights and reading positions are not reassigned to a downloaded replacement.

Before publication, cancellation, revoked account permissions, disabled connections and changes to allowed file formats stop further work. Pausing the feature stops dispatching downloads. An already authorized file entering ingest can still finish and record its receipt while the feature is paused. Its original request information is retained across a failed acknowledgment so retry can recover the same import.

## Credentials and recovery

Connection configuration and catalog selection URLs are encrypted in the application database. Stored credentials are not returned to the browser. Include the `acquisition.key` file beside `app.db` in your private configuration backup: restoring the database without its original key prevents CWNG from reading those connections. CWNG does not silently generate a replacement key for existing encrypted data.

Credentials are sent only to their configured origins. Redirects do not automatically inherit them. For a private catalog, use the advanced origin and network settings to authorize the intended destination. These settings are administrator controls; catalog entries cannot expand them.

Catalog selections expire. Expired selections that are not referenced by a request are removed in bounded batches as that account browses. There is also a limit on active selections per account. If reached, wait for older selections to expire and browse again. Selections backing durable requests are preserved for recovery.
