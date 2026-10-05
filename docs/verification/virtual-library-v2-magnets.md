# Direct pure-v2 magnet verification

## Product contract

A private source-offered magnet with exactly one `urn:btmh:1220` topic carries
one full SHA-256 expected identity. Admission keeps the existing 8192-character,
32-pair, `xt`/`dn`/`tr`, tracker-origin and credential boundaries. Unsupported
multihash algorithms or lengths, duplicate topics, btih+btmh co-presence, direct
peers and unknown parameters are refused. The original URI is sent unchanged.

qBittorrent must report a libtorrent 2.0 engine and WebUI API 2.11.2–2.15.1
before the durable submission callback or torrent add. API 2.11.2 is the
released 5.0.0 boundary for the required `has_metadata` flag; released 4.5.5
(API 2.8.19) and 4.6.7 (API 2.9.3) lack it. This narrower API qualification
applies only to direct pure-v2 magnets, preserving ordinary v1 and file behavior. LT1, Transmission and unknown builds refuse without an
attempt start, attempt key or external identity. A compatible-engine retry can
then issue its first submission. Existing read-only collision checks remain
before the callback; an adopted shared attempt never issues another add.
Engine/API facts are refreshed after collision lookup before a fresh fence;
separate daemon API calls still leave a non-atomic concurrent restart window.

The full 64-hex expected digest and qBittorrent's 40-hex native torrent ID have
different purposes. The full digest comes from the original private offer on
every poll, restart and shared-attempt recovery. No source is refetched and no
shortened ID is promoted to a full hash. The adapter queries owned category/tag
and native ID, then `torrents/properties`, before `torrents/files`:

- The returned native `hash` must be valid and equal the owned row and expected
  shortened ID.
- `has_metadata` must be boolean when present. Absent/false metadata remains
  pending; empty strings, numbers and other nonboolean values fail explicitly.
- A missing/empty full `infohash_v2` remains pending. A supplied full hash must
  have valid SHA-256 hex framing and equal the full expected digest. A distinct
  digest sharing the same first 40 characters still fails.
- Files are read only after usable metadata and full equality. Existing settled
  state, every-file completion, contained regular-file selection, byte-copy,
  normal processing and private receipt requirements then apply.

Missing metadata is bounded by the existing waiting deadline. Malformed
properties or identity disagreement fails the CWNG request while preserving its
accepted/uncertain durable submission fence. Manual Retry reconciles the same
owned attempt; it does not add another torrent. Client-owned files, permissions,
jobs and seeding policy remain unchanged.

HTTP links redirecting to a new pure-v2 magnet are refused before a durable
attempt or submission. This slice has no durable full-topic authority for that
mutable redirect. Existing HTTP-to-v1-magnet redirects remain supported. Dual
topics are deferred because their co-presence cannot prove same-hybrid identity.
No schema, dependency, license, runtime service URL or frontend change is needed.

## Primary protocol evidence

[BEP 9](https://www.bittorrent.org/beps/bep_0009.html) defines the magnet topics;
[BEP 52](https://www.bittorrent.org/beps/bep_0052.html) defines full v2 SHA-256
and permitted shortened identifiers. Released
[libtorrent 2.0.11 magnet parsing](https://github.com/arvidn/libtorrent/blob/v2.0.11/src/magnet_uri.cpp)
accepts the SHA-256 multihash. qBittorrent 5.0.0's
[add action](https://github.com/qbittorrent/qBittorrent/blob/release-5.0.0/src/webui/api/torrentscontroller.cpp#L650-L755)
passes the URI through
[AddTorrentManager](https://github.com/qbittorrent/qBittorrent/blob/release-5.0.0/src/base/addtorrentmanager.cpp#L48-L85)
and [TorrentDescriptor](https://github.com/qbittorrent/qBittorrent/blob/release-5.0.0/src/base/bittorrent/torrentdescriptor.cpp#L48-L110)
to the native parser. Its
[properties action](https://github.com/qbittorrent/qBittorrent/blob/release-5.0.0/src/webui/api/torrentscontroller.cpp#L425-L489)
returns full `infohash_v2`, native `hash`, and boolean `has_metadata`.
These are specification and released source observations, not running-client or
peer-transfer evidence. The version string alone does not establish LT2. The released
[5.0.0 API version](https://github.com/qbittorrent/qBittorrent/blob/release-5.0.0/src/webui/webapplication.h)
and [4.6.7 properties](https://github.com/qbittorrent/qBittorrent/blob/release-4.6.7/src/webui/api/torrentscontroller.cpp)
provide the metadata compatibility boundary.

## Bounded implementation evidence

OBSERVED on frozen16 `97fc716`: root's original released 2.0.11 generator URI
and trackerless variant both failed desired admission with `invalid_magnet`;
the unchanged v1, malformed and dual-topic controls passed. Root retains those
held scripts and logs outside the repository.

The owning behavioral suite is `tests/unit/test_acquisition_v2_magnets.py`.
It embeds that original URI and full digest, observes exact submission wire
values, uses real SQLite attempts and fresh worker instances, and observes file
enumeration/publication. Initial deep probes were seen red before product edits:
already-fenced known-ID and tag-only recovery read files despite pending,
malformed or mismatched full metadata. The corrected probes prevent those reads.
The suite also covers incompatible-engine no-effect/retry, lost acknowledgement,
pending restart, shared adoption, prefix collision, and HTTP-to-v2 refusal.

Publication probes observe the normal worker's watched-file boundary using an
owned legal PDF and modeled client responses. They preserve original bytes and
0640 client mode. They do not substitute for actual Calibre import, Global
Library membership, private receipt validation or a browser flow. Adjacent
parser/client/worker/Usenet/MOBI tests protect the existing behavior; only the
obsolete test that refused a valid single btmh topic is updated to the new scope.

## Current native and independent verification

OBSERVED on the reviewed three product sources: the original generated and
trackerless URI acceptance controls passed; seven held API/metadata recovery
checks and 281 owning and adjacent behavioral checks passed. Older or malformed
API refusal was seen red before the prerequisite fix. Seven deeper compatibility
and post-collision engine/API controls were also seen red before that fix.

The normal full acquisition runtime wrapper passed with the current integration
harness. Its new matrix contains 14 cases: 13 successful cases produced 14
private Calibre receipts, with four pre-submission refusal controls and one
collision retry. Captured packets from the successful outcomes were joined
separately to the released sessionless libtorrent 2.0.11 creator/parser,
including exact info bytes, piece layers, payloads, full SHA-256 and shortened
native IDs. Existing acquisition controls and actual old library, Calibre
bookmark and application reading state remained intact. The first full run
failed before this matrix because the host launcher selected Python without
an existing dependency; that failure was retained, then the existing virtual
environment was selected without changing the product or assertions.

The independent reviewer reports scoped CLEAR after 17 wire/SQLite probes,
including the post-collision engine change finding and compatibility fix.
The actual scoped security review reports CLEAR on these three source hashes.
Those review findings are independent evidence, separate from the runtime
observations above. The remaining concurrent restart window after a final
read-only daemon response is documented, not claimed atomic.

Native writes exercised production admission functions on a host without s6;
they are not evidence of HTTP request admission or container service health.
The French browser harness separately exercises session/CSRF-protected HTTP
requests, selection, production worker, processor, private receipts and clicked
downloads with controlled worker availability and manual worker advancement.
Its loopback connections are privileged fixture seeds; the public source probe
refuses loopback as expected. This host setup does not establish an unmodified
candidate container boot or s6 service availability.

OBSERVED: the French production SPA passed four cells, at root and `/books`
on 1280-pixel desktop and 375-pixel phone viewports. Real request and explicit
selection POSTs returned 202 with CSRF; pending metadata stayed gated until the
full native hash matched. Two legal EPUB choices excluded padding. The selected
book reached a private receipt, Open book and a clicked download with the same
source, stored and receipt hash. All 16 main-content axe states had no serious
or critical violations, and authenticated error streams were empty; expected
signed-out 401s were recorded separately. Source/client disable, revoked account
and cross-owner choice/receipt controls held. Current packets from all four
cells were independently joined to the released sessionless creator/parser and
actual downloaded source bytes. Actual phone choice/book JPEGs were inspected.
The two initial harness failures are retained: one did not honor production's
30-second pending-metadata backoff; another encountered an existing capture
directory. Their corrections preserved product code and acceptance assertions.

Current Linux/browser CI and anonymous immutable selected-image verification
remain delivery gates. No running client
daemon, peer transfer, whole ARM execution, public tracker, provider, household,
physical device or deployment is claimed by these native or source checks.


Current Docker CI exposed a full-matrix subprocess timeout at its inherited 300-second bound (one timeout, 133 passing integration tests). The full probe transport now allows 1200 seconds and the sequential integration step 45 minutes; every probe assertion and individual processor limit is unchanged. Original failed logs are retained. Native and French runtime evidence predates this transport-only adjustment and is carried by identical executed product/probe bytes; fresh candidate-image CI must exercise the new transport bound before delivery.
