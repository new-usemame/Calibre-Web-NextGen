# Book sources: additional download clients

Slice 4, 2026-09-30. This change adds NZBGet, qBittorrent and Transmission to the existing connection/offer/job/import boundaries. It requires the same default-off feature, explicit account grant, enabled client/indexer, immutable selection revision, bounded fetch and durable ingest receipt as the existing OPDS/SAB implementation. No database migration or household deployment is involved.

## Released protocol references

- NZBGet [append documentation](https://nzbget.com/documentation/api/append/), released [24.8 RPC implementation](https://github.com/nzbgetcom/nzbget/blob/v24.8/daemon/remote/XmlRpc.cpp) and [26.3 RPC implementation](https://github.com/nzbgetcom/nzbget/blob/v26.3/daemon/remote/XmlRpc.cpp). `AutoCategory` is inserted before `PPParameters` starting at 25.2; earlier releases retain ten positional arguments. Automatic recategorization is disabled.
- qBittorrent [WebUI API 5.0 documentation](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-5.0)), released [5.2.4 session implementation](https://github.com/qbittorrent/qBittorrent/blob/release-5.2.4/src/webui/webapplication.cpp) and [add endpoint](https://github.com/qbittorrent/qBittorrent/blob/release-5.2.4/src/webui/api/torrentscontroller.cpp). Actual 5.2.4 login returned 204 with a `QBT_SID_<port>` cookie, and add returned a structured JSON acknowledgement. Legacy `SID`/`Ok.` contracts remain in the fixtures. Authentication is retried once on a definitive 401/403 response; accepted submission returns its known hash before any subsequent poll.
- Transmission released [4.0.6 RPC specification](https://github.com/transmission/transmission/blob/4.0.6/docs/rpc-spec.md) and [4.1.3 RPC specification](https://github.com/transmission/transmission/blob/4.1.3/docs/rpc-spec.md). The adapter intentionally uses the retained legacy RPC naming and one bounded 409 session-ID handshake, not the moving main-branch protocol.

`tests/fixtures/acquisition-clients.json` pins six released compatibility cases. Accepted bounds are NZBGet 21.x–26.x, qBittorrent API 2.8.0–2.15.1, and Transmission legacy RPC 17/18/19. Only the current versions below were run as live software; fixtures do not establish live coverage of every accepted version.

## Observed real-client flow

The owned local rig used CWNG, the local fixture indexer/NNTP server, a compact local tracker, and a real Transmission seeder on one Docker network. The fixture generated its own original EPUB and companion text; neither provider access nor third-party material was required. DHT, peer exchange and local peer discovery were disabled in the torrent rig. No household service, public tracker/indexer or real Usenet provider was used.

| Client | Live version | Real path | Result |
| --- | --- | --- | --- |
| NZBGet | 26.3 | Local indexer NZB → local yEnc NNTP article → completed postprocessing → copy → ingest | Imported receipt, book 225 |
| qBittorrent | 5.2.4, WebUI API 2.15.1 | Single-file `.torrent` and multi-file magnet → local seeder → all files complete → copy → ingest | Two imported receipts, book 225 |
| Transmission | 4.1.3, legacy RPC 19 | Single-file `.torrent` and multi-file magnet → local seeder → all files complete → copy → ingest | Two imported receipts, book 225 |

All five receipts record source SHA-256 `031501d9df65af51445fbaa9838df8e1925676e2e49ae1e9e440878cb7ac40ac`. Both torrent clients retain the completed EPUBs and companion files; NZBGet retains its completed EPUB. qBittorrent reports both torrents at progress 1/amount-left 0 with inherited ratio/time limits (`-2`). Transmission reports both at percentDone 1/leftUntilDone 0 with default seed modes. No seeding-policy fields are sent by CWNG.

A duplicate click returns the same per-account job. The five receipt rows persist through a CWNG container recreation. Real wrong credentials return `needs_auth` for every client; a closed client port returns `source_unreachable`. The actual Requests page displays the five imports, and book 225 renders the original text in the EPUB reader at desktop and phone widths.

Restart while incomplete, multiple-account adoption, failed/missing remote jobs, no usable book, seven-day staleness, uncertain acceptance, expired sessions and partial companion files are exercised at the isolated worker/transport boundary. Live software proves successful download/import, persisted receipt recovery and real auth/down behavior; it does not simulate every failure with real remote jobs. Older failed rig-debug attempts are preserved as failed history rather than presented as passing proof.

## Independent refutation and UI

Fresh-context backend review reproduced and drove fixes for a reported directory authorizing an unreported book, failed torrent Retry losing ownership, encoded source credentials in a tracker URL, accepted qBittorrent submission losing its fence during post-add auth failure, and a repaired attempt refusing a new subscriber. The final narrow state/compatibility set passed 17 cases; an additional independent v1 format pass passed 11 cases, including both actual generated fixture descriptors. Tracker authority was checked by the production validator tests; the final reviewer verdict is not a complete network-security audit.

A separate UI reviewer ran multiple rounds on the rebuilt bundle: three new client forms plus Torznab tracker setup, desktop/375px phone, light/dark. Both initial and final 16-cell scoped axe matrices had zero violations/incomplete checks, no field overflow and no page errors. Keyboard walkthroughs reached all fields and Add; focused phone controls stayed visible. Saved-password edit hydration remains blank. Transmission no-auth hides credentials. Browser-intercepted save/probe failures announce with `role=alert`; the tracker input references both its guidance and the form error.

Compressed actual viewport captures are retained with the local verification artifacts, including the real Requests and book-reader flow. Safari/VoiceOver and physical touch were not exercised. Arbitrary lower-scroll full-page axe can report target-size on a partly obscured earlier Name input; this also reproduces on the unchanged OPDS form. Canonical form-top scans and focused keyboard geometry pass. This inherited header/scan-position interaction is preserved as a limitation, not silently discarded.

## Limits and deferred scope

Tracker origins are explicit administrator authority for submitted descriptor URLs. They do not sandbox a client's later peer/DHT/DNS networking. Web seeds, alternate magnet fetch URLs and bootstrap-node metainfo are rejected. Only v1 torrents, at most 1,000 reported files, and exactly one usable EPUB/PDF are supported; no archive extraction, multi-book selection or v2/hybrid support is claimed.

Shelfmark is deferred: there is no inexpensive completion contract in this slice that returns attributable CWNG import proof. Anna's Archive is excluded. No release, merge or deployment is part of this PR; operator review is the next gate.

## Local checks

- Final acquisition regressions: 362 passed, including the two unsupported-format cases. New client behavior was first seen red with missing adapters; independently reproduced defects and the hybrid/symlink cases have preserved red-to-green results.
- Desktop/phone connection browser suite: 17 passed against the rebuilt rig. Frontend build/typecheck passes; earlier frontend unit run passed 192 cases.
- All 28 translation catalogs compile. Twenty-six new strings are translated in de/fr/hu/nl. Authenticated live catalog reads return those translations, and an actual German-profile session renders the new fields with `html lang=de`; its temporary fixture-account locale change was restored. The image contains compiled catalogs and its SPA bundle.
- Full local unit/smoke suite ran **once**: 10,231 passed, 103 skipped, four failed. The two new documentation failures (classifier module count and changelog fragment category) were fixed; their focused suites pass 40 and 11 cases respectively. The other two failures are the previously reproduced macOS SQLite disk-I/O failures in `test_2291_replaced_metadata_db_reconnects.py` (`test_a_file_still_changing_is_adopted_once_it_settles`, `test_checks_are_rate_limited_between_requests`). No test was skipped or weakened to obtain a passing result. Final Linux CI is required on the PR.
- The copied development fixture library contains older deliberately invalid book files; its background KEPUB backfill logs conversion failures for those unrelated books. The new source hash, five ingest receipts and actual reader flow succeed. The log record preserves those unrelated errors instead of claiming a globally clean fixture log.

## Independent review fixes, 2026-10-01

Review of #2391 identified failure paths absent from the original real-client happy paths. The fixes cover Transmission tracker errors and oversized discovery, NZBGet busy history and PAR/script completion, settled torrent states and final-file moves, and definite qBittorrent rejection. HTTP redirects ending in magnets now return a descriptor for validation without requesting the magnet URL.

Released NZBGet [26.3 status implementation](https://github.com/nzbgetcom/nzbget/blob/v26.3/daemon/queue/DownloadInfo.cpp) establishes `SUCCESS/PAR` as successful PAR processing and `WARNING/SCRIPT` as a script failure after download/postprocessing; the completed EPUB/PDF must still validate. Its released [queue/history implementation](https://github.com/nzbgetcom/nzbget/blob/v26.3/daemon/remote/XmlRpc.cpp) has no ID filter or pagination. Thus only those full snapshots receive a 32 MiB cap, with the existing row/deadline limits retained. Transmission uses minimal discovery fields and single-hash file queries.

The added busy-server regression runs both adapters through the production child transport against an owned local HTTP server. It serves 2,001 entries, with full history/file metadata exceeding 2 MiB, and verifies setup, persisted-ID lookup and uncertain-name recovery. Both adapters failed with `file_too_large` before the fix. Status/move/rejection regressions also failed before the fix. Durable worker tests prove absent final files remain downloading, then publish once after arrival or expire at the deadline without resubmitting. Negative cases retain traversal, dangling-symlink, non-file, alternate-fetch and untrusted-tracker rejection. A real child receives a terminal HTTP-to-magnet redirect; ordinary file downloads still reject it.

Low-cost findings addressed: legacy `Fails.` is definite rejection; descriptor-to-magnet resolution is supported. Explicit tracker authority and request-time `.torrent` validation are retained and explained in setup documentation. Broader metainfo-extension compatibility is deferred until concrete released-client fixtures establish that the fields carry only supported semantics. No new UI strings or database schema changes are needed. Full CI on the revised PR head is the remaining delivery gate.
