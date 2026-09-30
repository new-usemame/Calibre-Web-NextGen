# Book sources (Beta)

Find books connects an administrator's catalogs to CWNG's library. Accounts with access can browse a connected catalog, choose an available EPUB/PDF or NZB release, and request its import. Indexer releases download through a connected SABnzbd client. The file goes through CWNG's existing ingest and configured processing before appearing in the Global Library. Accounts using My Library can also add the resulting book to their own selection.

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
3. Choose **OPDS catalog** and add a connection with a name and an **OPDS catalog address**. Select no sign-in, username and password, or a token, as the catalog requires.
4. Choose **Test connection**, then mark the catalog **Available to users**. A successful test confirms only that CWNG can read the catalog; it does not download a book.
5. Under **Who can use it**, grant the intended accounts **Can browse and request**. Add **No approval needed** only for accounts that should bypass approval; everyone else's requests appear under **Waiting for approval** for an administrator to approve or reject.
6. The granted account opens **Find books**, selects the catalog, and browses or searches when the catalog advertises search. They choose a format, and the book is requested or imported according to their permission.

New connections start disabled. The setup page reports runtime problems such as an unavailable ingest service, a missing connection key, or no supported file formats. Correct those before requesting imports. This Beta requires CWNG's container ingest service and existing background scheduler.

The public Project Gutenberg OPDS entrypoint is `https://www.gutenberg.org/ebooks/search.opds/`. Gutenberg publishes its supported machine-readable entrypoints in its [catalog documentation](https://www.gutenberg.org/ebooks/offline_catalogs.html). Use a provider's documented catalog endpoint rather than its HTML search page.

## What is supported

| Capability | Current support |
| --- | --- |
| Catalogs | OPDS 1 Atom and OPDS 2 JSON; Newznab/Torznab search, including Prowlarr and Jackett presets |
| Browsing | Catalog navigation, groups, facets and pagination |
| Search | Advertised OpenSearch descriptions and supported OPDS 2 keyword templates |
| Files | Direct EPUB/PDF links and NZB releases containing exactly one completed EPUB/PDF, intersected with allowed upload formats |
| Download client | SABnzbd; waits for successful postprocessing before import |
| Authentication | None, HTTP Basic, or Bearer credentials scoped to configured origins |
| Local services | Explicit administrator configuration of private origins and network ranges |
| Import | Existing CWNG ingest, configured repair/conversion, durable import receipt and account attribution |

A catalog may list books without a supported direct file. Purchase, borrowing, DRM, previews, HTML landing pages and indirect acquisition flows are not presented as downloadable files. Inline catalog artwork is optional and is not downloaded.

Anna's Archive and torrent download clients are not implemented by this Beta. Torznab torrent results can be discovered but cannot be requested through SABnzbd. They are not shown as working providers. The framework separates catalog discovery, file transport and library ingestion so additional protocol adapters can reuse the same permission, request and import boundaries.

## Connect an existing Usenet stack

1. Add a **SABnzbd download client** first. Enter its API endpoint (for example `http://sabnzbd:8080/api`), full API key, and an existing category such as `books`. The restricted NZB key cannot read queue/history and is unsuitable.
2. Map the completed folder between the two services. If SAB sees `/downloads/complete` and CWNG mounts that same folder at `/completed`, enter those two absolute paths. Mount the completed folder read-only into CWNG. The category's output directory must be under the remote folder. POSIX paths are required; relative paths, symlinks, traversal, and ambiguous folders with multiple books are refused.
3. Test the client. CWNG checks the category, queue/history access, SAB's completed directory and category output, and whether its mapped local folder is readable. Switch the client on.
4. Add a **Newznab / Torznab indexer**, select the Prowlarr, Jackett, or direct protocol preset, and bind the enabled SAB client. Use the protocol API endpoint, rather than the management API or web interface. For Prowlarr this is the chosen indexer's `/1/api` endpoint; substitute its actual indexer ID. Enter the API key separately and a book category advertised by that endpoint, usually `7020` (EBook).
5. For services on your private network, enable the local-network setting. When Prowlarr redirects NZB downloads to another local indexer, add that destination's exact origin under **Additional local download origins**. Each destination must be explicit. Source credentials do not follow a redirect to another origin.
6. Test the indexer, then switch it on. The test verifies advertised search and category support and performs an authenticated search. A granted account can now select it in **Find books**, search, and request an NZB release.

These presets use the same protocol; they do not create an indexer or NNTP provider inside Prowlarr, Jackett, or SAB. Configure those services yourself and use sources you are authorized to access. SAB performs downloading and any repair/unpacking already configured there. CWNG does not unpack archives or execute scripts from releases. Completed folders must contain exactly one usable EPUB/PDF, so multi-book bundles require separate handling.

SAB receives the NZB bytes, not an indexer URL or its credentials. CWNG stores the remote job identity before continuing and reconciles it after a restart. A lost submit response is handled conservatively: retry looks for the exact owned job and does not blindly submit again. If its acceptance cannot be established, it reports uncertain submission for administrator investigation. Keep the remote queue/history entry until CWNG has completed import.

## Edit or remove connections

**Edit** refuses a stale form if another administrator has changed the connection. Reload its settings before saving. It keeps the current credential when its field is blank, rotates the configuration revision, and switches the connection off. Test it and enable it again. Previous search selections expire. Outstanding requests must finish or be cancelled before an edit or deletion; this prevents queued work from silently using different credentials or paths. Deletion removes the connection and its stored credential while preserving request history.

## Requests and existing books

Requests belong to the account that created them. Requests for the same indexer release resolve to one request per account. Separate accounts retain private request histories while reusing the same SAB download. A rejected or cancelled release remains in that account’s history; another click returns that request rather than bypassing the earlier decision. Repeating the same submission after an uncertain response returns the original request. Accounts cannot use another account's catalog selections or inspect its requests.

Imported means that CWNG has recorded the actual library book IDs and completed its import receipt. A download finishing alone does not mean the book is available. The book links still follow normal library visibility rules.

Acquisition preserves an existing same-format edition when Calibre matches its title and author. It does not overwrite that edition, even when ordinary ingest is configured to overwrite duplicates. Its result identifies that the existing edition was retained. A different format may create a separate library record. Existing highlights and reading positions are not reassigned to a downloaded replacement.

Before publication, cancellation, revoked account permissions, disabled connections and changes to allowed file formats stop further work. Pausing the feature stops dispatching downloads. An already authorized file entering ingest can still finish and record its receipt while the feature is paused. Its original request information is retained across a failed acknowledgment so retry can recover the same import. Cancelling a request stops its import; it does not delete or cancel SAB data, because another account may share that download.

## Credentials and recovery

Connection configuration and catalog selection URLs are encrypted in the application database. Stored credentials are not returned to the browser. Include the `acquisition.key` file beside `app.db` in your private configuration backup: restoring the database without its original key prevents CWNG from reading those connections. CWNG does not silently generate a replacement key for existing encrypted data.

Credentials are sent only to their configured origins. Redirects do not automatically inherit them. For a private catalog, use the advanced origin and network settings to authorize the intended destination. These settings are administrator controls; catalog entries cannot expand them.

Catalog selections expire. Expired selections that are not referenced by a request are removed in bounded batches as that account browses. There is also a limit on active selections per account. If reached, wait for older selections to expire and browse again. Selections backing durable requests are preserved for recovery.

## Offline verification fixture

Developers can run `tests/fixtures/virtual_library_fixture.py` on an isolated Docker network. It exposes a local Newznab endpoint on port 8090 and an NNTP server on port 8119, generates an original EPUB, and serves one yEnc article. Its disposable API key is `fixture-key`. Add it as a Generic Newznab source in Prowlarr, configure that NNTP server in SABnzbd, and connect CWNG to the two real services. The fixture has no outbound requests. `/counts` reports search, descriptor, and article activity so a restart or second account's request can prove it reused a download rather than submitting another.
