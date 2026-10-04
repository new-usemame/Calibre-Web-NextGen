# Book sources (Beta)

Find books connects an administrator's catalogs to CWNG's library. Accounts with access can browse a connected catalog, choose an available direct EPUB/PDF or opted-in DRM-free MOBI 6 book, NZB or torrent release, and request its import. Indexer releases download through a connected SABnzbd, NZBGet, qBittorrent or Transmission client. The file goes through CWNG's existing ingest and configured processing before appearing in the Global Library. Accounts using My Library can also add the resulting book to their own selection.

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

## Search connected catalogs together

When two or more catalogs are available, choose **All catalogs** in **Find books** and enter a keyword. CWNG searches four catalogs at a time. Each result group keeps its catalog name, editions, languages and formats; matching titles from different sources remain separate so you can choose the intended edition.

Results arrive as each source answers. A failed source reports the failure and has its own **Try again** button; other results stay available. A catalog that does not advertise search has a **Browse this catalog** button. **Search remaining catalogs** starts the next group of up to four sources. Loading the page does not start a shared search.

A result's navigation or next page opens that exact page in individual catalog browsing. Download/request buttons use the result's own source and the existing approval and import flow. **Refresh catalogs** reloads the available connections. Withdrawing or editing a source clears its results for the current query; re-enabling it requires a fresh search. A new query clears earlier results and cancels pending browser reads.

## What is supported

| Capability | Current support |
| --- | --- |
| Catalogs | OPDS 1 Atom and OPDS 2 JSON; Newznab/Torznab search, including Prowlarr and Jackett presets |
| Browsing | Catalog navigation, groups, facets, pagination and explicitly opened OPDS 1 complete entries and OPDS 2 publication details |
| Search | Individual or shared keyword search using advertised OpenSearch descriptions, supported OPDS 2 templates and Newznab/Torznab book search |
| Files | Direct EPUB/PDF and explicitly enabled DRM-free MOBI 6 links, NZB releases, v1 torrent files/magnets and reviewed hybrid torrent files containing completed EPUB/PDF or explicitly enabled DRM-free MOBI 6 books, intersected with allowed upload formats |
| Download client | SABnzbd/NZBGet after successful postprocessing; qBittorrent/Transmission after every file finishes downloading |
| Authentication | None, HTTP Basic, or Bearer credentials scoped to configured origins |
| Local services | Explicit administrator configuration of private origins and network ranges |
| Import | Existing CWNG ingest, configured repair/conversion, durable import receipt and account attribution |

A catalog may list books without a supported direct file. Purchase, borrowing, DRM, previews, HTML landing pages and indirect acquisition flows are not presented as downloadable files. Inline catalog artwork is optional and is not downloaded.

Anna's Archive is outside this Beta. Shelfmark is deferred because this slice has no inexpensive completion contract that attributes its result to a CWNG request. Torrent results require qBittorrent or Transmission; Usenet clients accept NZB results. The framework separates catalog discovery, file transport and library ingestion so additional protocol adapters can reuse the same permission, request and import boundaries.

OPDS 1 partial catalog entries may advertise an alternate complete-entry document. Open that detail explicitly to see its metadata and supported direct files. Complete entries retain the same transport, account-bound selections, approval and import policy as ordinary feeds; they do not follow their own self-links.

OPDS 2 metadata may supply publication titles and contributor names in several languages. CWNG chooses the saved account locale, trying the exact tag, a matching regional variant and language parents, then English or a stable alphabetical fallback. Every supplied variant is validated. This changes display text only: the edition’s declared languages, identifiers, downloaded bytes and import identity stay unchanged.

A summary can advertise a `self` or `alternate` link of type `application/opds-publication+json`. Open that link explicitly to read its details and see any supported direct EPUB/PDF offer. CWNG does not fetch details eagerly or turn buy, borrow, subscribe, sample, preview, indirect or templated offers into downloads or automatic detail reads. A detail document’s own self-link is suppressed. The same connection transport policy and owner-bound opaque selections apply. See [OPDS metadata and detail verification](verification/virtual-library-opds-publications.md).

## Connect an existing Usenet stack

1. Add a **SABnzbd download client** first. Enter its API endpoint (for example `http://sabnzbd:8080/api`), full API key, and an existing category such as `books`. The restricted NZB key cannot read queue/history and is unsuitable.
2. Map the completed folder between the two services. If SAB sees `/downloads/complete` and CWNG mounts that same folder at `/completed`, enter those two absolute paths. Mount the completed folder read-only into CWNG. The category's output directory must be under the remote folder. POSIX paths are required; relative paths, symlinks, traversal, and ambiguous folders with multiple books are refused.
3. Test the client. CWNG checks the category, queue/history access, SAB's completed directory and category output, and whether its mapped local folder is readable. Switch the client on.
4. Add a **Newznab / Torznab indexer**, select the Prowlarr, Jackett, or direct protocol preset, and bind the enabled SAB client. Use the protocol API endpoint, rather than the management API or web interface. For Prowlarr this is the chosen indexer's `/1/api` endpoint; substitute its actual indexer ID. Enter the API key separately and a book category advertised by that endpoint, usually `7020` (EBook).
5. For services on your private network, enable the local-network setting. When Prowlarr redirects NZB downloads to another local indexer, add that destination's exact origin under **Additional local download origins**. Each destination must be explicit. Source credentials do not follow a redirect to another origin.
6. Test the indexer, then switch it on. The test verifies advertised search and category support and performs an authenticated search. A granted account can now select it in **Find books**, search, and request an NZB release.

These presets use the same protocol; they do not create an indexer or NNTP provider inside Prowlarr, Jackett, or SAB. Configure those services yourself and use sources you are authorized to access. SAB performs downloading and any repair/unpacking already configured there. CWNG does not unpack archives or execute scripts from releases. Single-book completions import automatically; completed multi-book bundles wait for your explicit choice as described below.

SAB receives the NZB bytes, not an indexer URL or its credentials. CWNG stores the remote job identity before continuing and reconciles it after a restart. A lost submit response is handled conservatively: retry looks for the exact owned job and does not blindly submit again. If its acceptance cannot be established, it reports uncertain submission for administrator investigation. Keep the remote queue/history entry until CWNG has completed import. A download still unfinished after seven days fails with a waiting-limit message, releasing the connection for administration. It is not automatically resubmitted. A manual retry after SAB reports a definite failed download creates a new durable attempt; an uncertain submission retains its identity. Definite submission rejection also resolves requests that adopted that attempt.

## Connect NZBGet, qBittorrent or Transmission

Add the download client first, map its completed folder into CWNG read-only, test it, then enable it. Bind the indexer to that enabled client. New connections remain disabled until enabled explicitly.

| Client | Endpoint and credentials | Category, label and path check |
| --- | --- | --- |
| NZBGet | JSON-RPC endpoint, usually `http://nzbget:6789/jsonrpc`; username/password able to read `version`, `config`, `listgroups` and `history`, and submit `append` | Existing category; its destination (or global `DestDir`) must lie within the remote mapping. CWNG waits for successful completed postprocessing. |
| qBittorrent | WebUI root, usually `http://qbittorrent:8080/`, or its `/api/v2` root; username/password | Existing category; its save path, or default save path if empty, must lie within the mapping. Submissions use the configured category and folder with automatic torrent management disabled for that torrent. |
| Transmission | RPC endpoint, usually `http://transmission:9091/transmission/rpc`; username/password or explicit no authentication on an appropriately protected service | Configured download folder and label. CWNG checks RPC field access and reported free space for that folder. Labels are attached to new torrents; there is no pre-existing category to create. |

Usenet results can use SABnzbd or NZBGet. Torznab results can use qBittorrent or Transmission. Set **Allowed torrent tracker origins** on the indexer to the exact trusted HTTP, HTTPS or UDP origins, for example `https://tracker.example, udp://tracker.example:6969`. Descriptors mentioning other trackers are blocked before submission. An empty list accepts no tracker URLs. Indexer keys are not passed to a tracker or client as source URLs. Web seeds, alternate magnet download URLs and metainfo bootstrap-node extensions are refused.

This authority controls tracker URLs in submitted descriptors. It does not sandbox the download client's subsequent peer, DHT or DNS networking. Configure the client's own networking and firewall for the sources you trust. A trackerless magnet may depend on the client's existing peer-discovery configuration.

V1 torrent files and magnets, plus self-consistent BEP52 hybrid files, are supported. A hybrid must describe the same safe files, lengths and ordering in both views, with exact piece-alignment padding and valid SHA-256 piece-layer roots. CWNG preserves its original descriptor bytes. qBittorrent requires a reported libtorrent 1.2 or 2.0 engine: 1.2 uses the v1 identity, while 2.0 uses the truncated v2 identity used by its WebUI. An unknown engine fails before submission and can be retried after the client is made compatible. Transmission keeps the v1 identity. Pure v2 files/magnets, unsupported extensions, traversal, symlinks and unreported paths are rejected. A multi-file torrent can contain EPUB/PDF books, or ordinary DRM-free MOBI 6 when enabled for that client, with non-book companions. CWNG checks aggregate completion **and every reported file**, including companions: entering a seeding state from a partial download is insufficient. It copies only the chosen reported regular book file. It never moves/deletes client files or jobs, sets seed ratios/time limits, or stops seeding. Cancelling a CWNG request affects its import, not the client download.

Reviewed [BEP47 file metadata](https://www.bittorrent.org/beps/bep_0047.html) is accepted: optional 20-byte per-file SHA1 hints, and hidden/executable (`h`/`x`) attributes on a single file. Existing multi-file padding is counted in the piece total, but a torrent must contain positive non-padding payload. CWNG requires the piece-hash count to match the total byte length and accepts only integer `private` flags of 0 or 1. It sends the original descriptor unchanged, preserving its v1 hash and private flag. File SHA1 hints do not determine book identity or import receipts; those use the actual selected bytes and SHA-256. Source permissions and executable attributes are not copied to the imported file, and CWNG does not execute downloaded content. Single-file padding, symlinks and other unsupported file/network extensions remain refused.

A missing queue/history entry, client error, unusable book or seven-day waiting limit produces an explicit failed request. No automatic resubmission occurs. Manual Retry reconciles a failed torrent by its original hash and owned tag/labels; repair it in the client first. A definite failed Usenet download gets a fresh durable attempt. Uncertain submission keeps its identity. Duplicate requests share the owned remote attempt across accounts while retaining private request histories and normal ingest receipts. A torrent already present outside that owned attempt is refused.

### Completed MOBI books

For SABnzbd, NZBGet, qBittorrent or Transmission, edit that connection and select **Allow completed DRM-free MOBI 6 books**. It is off by default and applies only to that client. The server's allowed upload formats must also include MOBI. This setting is separate from the direct-MOBI option on an OPDS catalog.

CWNG copies contained, completed ordinary DRM-free MOBI 6 books through the normal ingest and conversion settings. Encryption, KF8/unsupported variants and malformed files are refused before publication. A mixed EPUB/PDF/MOBI completion asks you to choose a book; its displayed format and resulting receipt reflect the selected source and actual stored format. Without conversion, a newly retained MOBI can be downloaded but has no web-reader action. Existing equivalent content may retain its already stored format, which the receipt reports. Client files, permissions, jobs and seeding controls remain unchanged.

Current account, source/client revision and format permissions are rechecked before private publication, including a recovered staged MOBI. Once a durable import capability has been issued, the existing receipt-reconciliation rules apply. See [the completed-client MOBI verification record](verification/virtual-library-client-mobi.md).

### API compatibility

These are deliberate protocol bounds, not an assertion that every version has been run live:

| Client | Accepted protocol | Pinned compatibility fixture | Live verification |
| --- | --- | --- | --- |
| NZBGet | Version 21.x–26.x JSON-RPC | Released 24.8 and 26.3; append gains `AutoCategory` at 25.2 | 26.3 |
| qBittorrent | WebUI API 2.8.0–2.15.1 | Released 5.0.0/API 2.9.3 (`SID`, `Ok.`) and 5.2.4/API 2.15.1 (204 login, `QBT_SID_<port>`, JSON add acknowledgement) | 5.2.4/API 2.15.1 |
| Transmission | Legacy RPC 17, 18 or 19 | Released 4.0.6/RPC 17 and 4.1.3/RPC 19, including 409 session-ID handshake | 4.1.3/RPC 19 |

The fixtures are in `tests/fixtures/acquisition-clients.json`. See [the client verification record](verification/virtual-library-clients.md) for released primary references and the isolated real-client proof.

## Choose books from a completed download

A completed owned Usenet or torrent download with one usable EPUB/PDF imports automatically. If it contains several books, its request waits for you to **choose a book from this download**. The list shows filenames, formats and sizes. Choose one explicitly; CWNG copies that file through the normal import pipeline and records its own receipt.

Use **Choose another book** on the original request to import another file from the same download, including after the first book has imported. Each selected book has an independent request, approval decision and receipt. Additional choices follow the account's current approval policy. Cancelling or rejecting a selected book leaves the other choices available; cancelling before any book is selected closes that bundle request. Repeating a choice returns its existing request, including a cancelled or rejected request.

All choices reuse the original owned client download, including choices by other permitted accounts. Each account has its own private candidate list and request history. CWNG does not alter client files or seeding. Only a completed import receipt provides an **Open book** link.

The list snapshots the completed files. Before copying a chosen book, CWNG checks that its path, size and bytes still match that snapshot. Missing, replaced, changed or unsafe files fail explicitly; CWNG never substitutes another book. Restore the original files before retrying. New contents under an already imported release need a separately identified fresh release; there is no automatic replacement of the saved choices.

Enumeration is bounded to 1,000 entries and 20 usable books, at most 100MiB per book and 512MiB in total. Larger or unsafe downloads fail with a useful error. No archives are extracted. Administrators can pause new choices while users continue to read their lists.

## Edit or remove connections

Changing the endpoint to a different origin requires explicitly re-entering the credential; a stored key cannot silently move to another server.

**Edit** refuses a stale form if another administrator has changed the connection. Reload its settings before saving. It keeps the current credential when its field is blank, rotates the configuration revision, and switches the connection off. Test it and enable it again. Previous search selections expire. Outstanding requests must finish or be cancelled before an edit or deletion; this prevents queued work from silently using different credentials or paths. Deletion removes the connection and its stored credential while preserving request history.

## Requests and existing books

Requests belong to the account that created them. Catalog requests for the same indexer release resolve to one original request per account; explicitly chosen books from that release get their own artifact requests. Separate accounts retain private request histories while reusing the same client download. A rejected or cancelled release remains in that account’s history; another click returns that request rather than bypassing the earlier decision. Repeating the same submission after an uncertain response returns the original request. Accounts cannot use another account's catalog selections or inspect its requests.

Imported means that CWNG has recorded the actual library book IDs and completed its import receipt. A download finishing alone does not mean the book is available. The book links still follow normal library visibility rules.

Acquisition first looks for an existing same-format record whose bytes match the selected artifact after import processing. For EPUB only, it can also retain a metadata-matched record when every regular file inside both packages has the exact same path and uncompressed bytes. Changes to ZIP compression, member order, timestamps, comments or empty directory records do not create a new edition. Changes to text, language metadata, fonts, rights files or any other resource do. Matching title, author, language or ISBN alone does not establish edition identity.

Existing files, highlights and reading positions remain attached to their original books. When CWNG retains an existing EPUB, **Open book** points to that original record; the receipt keeps the new downloaded-source hash and the hash of the actual original archive retained in the library. Different resources, unsupported packaging or exhausted comparison limits create a separate record, even when ordinary ingest is configured to overwrite duplicates. PDF and other formats continue to require exact file bytes. A further EPUB-only fallback accepts serialization changes to an ordinary single-rootfile `META-INF/container.xml` locator, such as namespace prefixes, attribute order or whitespace, while keeping every other resource exact. Extensions, multiple rootfiles, processing instructions, ambiguous locators and signed packages do not get this fallback. The comparison never rewrites either archive or normalizes publication metadata or content. See [EPUB packaging identity and limits](verification/virtual-library-epub-repackaging.md).

New imports and in-progress recovery reinspect older metadata-only retention results before completing an import. Already-completed requests keep their historical receipt and are not automatically reimported. The existing duplicate-request rules still apply.

Before publication, cancellation, revoked account permissions, disabled connections and changes to allowed file formats stop further work. Pausing the feature stops dispatching downloads. An already authorized file entering ingest can still finish and record its receipt while the feature is paused. Its original request information is retained across a failed acknowledgment so retry can recover the same import. Cancelling a request stops its import; it does not delete or cancel client data, because another account may share that download.

## Credentials and recovery

Connection configuration and catalog selection URLs are encrypted in the application database. Stored credentials are not returned to the browser. Include the `acquisition.key` file beside `app.db` in your private configuration backup: restoring the database without its original key prevents CWNG from reading those connections. CWNG does not silently generate a replacement key for existing encrypted data.

Credentials are sent only to their configured origins. Redirects do not automatically inherit them. For a private catalog, use the advanced origin and network settings to authorize the intended destination. These settings are administrator controls; catalog entries cannot expand them.

Catalog selections expire. Expired selections that are not referenced by a request are removed in bounded batches as that account browses. There is also a limit on active selections per account. If reached, wait for older selections to expire and browse again. Selections backing durable requests are preserved for recovery.

## Offline verification fixture

Developers can run `tests/fixtures/virtual_library_fixture.py` on an isolated Docker network. It exposes a local Newznab endpoint on port 8090 and an NNTP server on port 8119, generates an original EPUB, and serves one yEnc article. Its disposable API key is `fixture-key`. Add it as a Generic Newznab source in Prowlarr, configure that NNTP server in SABnzbd, and connect CWNG to the two real services. The fixture has no outbound requests. `/counts` reports search, descriptor, and article activity so a restart or second account's request can prove it reused a download rather than submitting another.

`tests/fixtures/virtual_library_torrents.py` extends that fixture with an original EPUB, a local Torznab endpoint, v1 single/multi-file descriptors and a compact local tracker. Run it with a real owned seeder on the same isolated network. It generates all payloads locally and makes no provider or public tracker requests.

### Download-client polling limits

Transmission setup requests hashes only. Recovery without a stored hash requests hashes and ownership labels, then requests file details for the single owned hash; it never retrieves every torrent's files. Discovery responses are capped at 8 MiB, individual/control responses at 2 MiB. Tracker warnings/errors do not invalidate complete peer downloads; local errors fail immediately, and an incomplete download still has the seven-day deadline.

NZBGet's released queue/history RPCs do not support pagination or ID filters. Queue and visible-history snapshots are capped at 32 MiB and 10,000 rows; other RPC responses remain capped at 2 MiB. Setup avoids retained history, and a known queued job is polled without fetching history. `SUCCESS/PAR` and `WARNING/SCRIPT` allow selection of the final book, which must still pass format/path validation. Damaged, repairable, move failures and user-marked successes do not bypass those status checks.

Torrent copies wait for a settled client state and for the reported final file to appear. A final-directory move can therefore defer an import without issuing a second download. A missing file remains subject to the same seven-day deadline; unsafe paths, symlinks and non-file results are refused. HTTP descriptor redirects ending in a magnet are resolved without fetching the magnet; its v1 hash, tracker origins and credential safety are validated before any submission.

Tracker origins must be configured explicitly; an empty list deliberately trusts no tracker URLs. Magnet trackers are checked during discovery. HTTP `.torrent` links are checked when their bounded descriptor is fetched for a request, so discovery does not fetch every result or spend provider quotas. Unknown metainfo extensions remain unsupported pending a reviewed fixture: accepting them blindly could introduce network or file semantics not covered by the v1 checks.

For an application downgrade, pause acquisition and settle or cancel outstanding bundle work first. Older workers do not understand selected-artifact jobs; do not resume them with unfinished bundle work. Keep the additive columns and manifest table intact. Upgrading preserves the existing permission migration marker and grants.

## Optional direct MOBI 6 books

Each OPDS connection starts with **Allow direct DRM-free MOBI 6 books** off. Edit that catalog in **Book sources** to enable it, save, test and enable the connection again. The server’s allowed upload formats must also include MOBI. Existing catalogs do not gain MOBI acquisition automatically. Disabling the catalog choice or narrowing the upload-format policy prevents new requests and is rechecked for queued work.

Normal ingest conversion settings still apply. A new MOBI import converted to EPUB can use the web reader. With conversion disabled, a new MOBI import keeps its MOBI format and can be downloaded; the web reader does not open MOBI. **Open book** goes to the resulting library record, whose available formats govern reading. An exact source already imported can retain its verified original record and format; changing conversion settings does not replace that record. Matching title and author alone do not overwrite another edition. Receipts retain the requested source hash and the hash of the actual stored artifact.

Ordinary MOBI 6 is supported through advertised direct catalog links (`application/x-mobipocket-ebook`) and completed NZB/torrent bundles when the matching connection option and current server formats allow it. The download-client option also starts off. Encrypted MOBI, KF8/AZW3 or hybrid books, HUFF/CDIC compression, PalmDOC-only PRC and other variants remain unsupported. Admission checks bounded Palm database/header framing and encryption flags; actual Calibre processing establishes the import. This does not remove DRM or promise that a header-valid malformed book will convert successfully.

The preflight uses at most 10,000 ordered records and a 2 MiB first record; it checks declared text size up to 200 MiB, contained title/EXTH records and PalmDOC compression 1/2. The direct download and administrator byte quota remain additional limits. These conservative resource caps can refuse unusual books even when another reader supports them. See [verification scope](verification/virtual-library-direct-mobi.md).
