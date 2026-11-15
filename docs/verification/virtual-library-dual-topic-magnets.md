# Paired-topic hybrid magnet verification

## Product contract

A direct private source-offered magnet may contain exactly one valid SHA-1
`urn:btih:` topic (40 hex or 32 base32 characters) and one full SHA-256
`urn:btmh:1220` topic, in either order. Both normalized full expected hashes
come from that original private offer. The original URI is submitted unchanged
as the `urls` form field, with no metainfo upload or descriptor fetch.
Co-presence in the URI does not establish that these hashes describe one hybrid.

The existing 8192-character, 32-query-pair, `xt`/`dn`/`tr`, tracker-origin and
credential restrictions remain. Repeated topics in either family, a third
topic, unsupported multihash tags/lengths, malformed hashes, direct peers and
unknown parameters are refused. Ordinary v1 and single-topic v2 magnets retain
their existing behavior.

Dual-topic strings require qBittorrent with a reported libtorrent 2.0 engine
and WebUI API 2.11.2–2.15.1 before an attempt start/key or add. Libtorrent 1.2,
Transmission, unknown engines/APIs and legacy adapters without preparation
support refuse even when the URI has a usable v1 topic. An upgrade followed by
Retry can issue the first exact original add. Compatibility is checked again
after the read-only collision lookup and before a fresh durable fence. Separate
external API calls retain the documented non-atomic daemon restart window.
Existing v1 magnets and v1/hybrid/pure-v2 files retain their compatibility rules.

Every worker invocation, including known-ID, tag-only, lost-acknowledgement,
accepted restart and shared-attempt adoption, reconstructs both expected hashes
from the durable private offer before polling. The qBittorrent adapter checks
owned category/tag and native ID, then the configured client's properties,
before requesting its files:

- Native `hash` must be valid 40-hex and match the owned row and shortened v2 ID.
- `has_metadata` must be boolean when supplied; only `true` permits file reads.
- Both full `infohash_v1` and `infohash_v2` must have their correct hex lengths
  and match the original pair. A pure-v2 response with no v1 remains pending;
  an incorrect v1 fails. A correct v1 with a distinct full v2 fails even when
  its first 40 characters collide with the owned native ID.
- Missing/empty identity or absent/false metadata waits only within the existing
  download deadline. Supplied malformed identity or mismatch fails immediately.

Accepted or uncertain attempt starts and keys remain fenced on metadata failure.
Retry reconciles that same attempt without another add. Only after full pair
confirmation do existing settled-state, completed-file, path containment,
selection, copy, normal processing and private receipt rules apply. Source files
are copied and retain their bytes, mode and client seeding policy.

A new HTTP link redirecting to a dual-topic magnet is refused before fencing:
this slice cannot recover original full-pair authority for a mutable redirect.
Existing HTTP-to-v1 redirects remain supported. No storage/schema change,
network metadata fetch, dependency, frontend or runtime service URL is added.
The final configured native properties contract is trusted under the existing
client model; it is not independent proof of daemon behavior or peer transfer.

## Protocol provenance

[BEP 9](https://www.bittorrent.org/beps/bep_0009.html) permits btih and btmh
topics for the same hybrid and describes v1 hex/base32 encoding. Released
[libtorrent 2.0.11's generator and parser](https://github.com/arvidn/libtorrent/blob/v2.0.11/src/magnet_uri.cpp)
emit both topics for a hybrid. The owning suite uses the historical sessionless
creator vector with full v1 `d3e6c98ab8696750d183a9fcf37d6178452bcce4` and
full v2 `6df5bebbb680f6a2eac792e6ed76c79eff25b108f21269f695cd6fdf247f21c9`,
including the original escaped tracker URI and trackerless control.

Released qBittorrent 5.0.0's
[properties action](https://github.com/qbittorrent/qBittorrent/blob/release-5.0.0/src/webui/api/torrentscontroller.cpp)
returns native `hash`, `has_metadata` and both full infohash fields. Its
[API version](https://github.com/qbittorrent/qBittorrent/blob/release-5.0.0/src/webui/webapplication.h)
is 2.11.2; the pinned released 4.5.5 and 4.6.7 properties lack the metadata flag.
These are source observations and historical vectors, not a new running-client
or peer-download check. The engine version alone does not qualify the API.

## Bounded owning evidence

`tests/unit/test_acquisition_dual_topic_magnets.py` drives the real worker and
SQLite repository against a local HTTP endpoint modeling released client
responses, using production child HTTP transfer. It observes URL-form requests,
attempt rows, file enumeration, private staging and the normal watched-file
publication boundary. Both original topics, reversed/base32 encoding, full
pair mismatches, missing identity, prefix collisions, pending deadlines,
accepted restart, lost acknowledgement, tag adoption, shared attempts,
compatibility downgrade/upgrade and collision retry are behavioral oracles.
The obsolete valid-dual refusal controls in the existing owning suites are
replaced by repeated/third-topic unsafe controls.

The probes use an original legal PDF, preserve exact source bytes and 0640 mode,
and exercise no public service. They establish the worker-to-watched-file
boundary; they do not establish actual Calibre imports, Global Library membership,
private receipts or browser behavior. Root owns held grading, independent review,
security, normal native/Calibre/private receipt and French SPA checks, current
CI and selected image evidence, and ordered delivery after actual slice17 merge.
Those remain separate gates; this implementation evidence does not claim them.

### Pre-submission collision review fix

For a new paired-topic magnet, the read-only qBittorrent collision query includes both the native shortened v2 ID and the full original v1 ID. A collision or malformed collision response stops before the SQLite submission fence and add call. The qualified engine/API check still runs again after the collision query. This closes the independently reproduced v1-only preflight gap; released qBittorrent source shows that a duplicate hybrid add can merge trackers when that option is enabled for a non-private torrent. No daemon-side mutation was executed in this verification.

Root held checks against the unchanged original source failed in all four meaningful collision cases; the corrected source passed 192 owning, adjacent and held checks, including original, trackerless and reversed/base32 topic forms and malformed collision responses. The initial review and security verdict are retained. The renewed independent review passed ten wire/SQLite probes on the fixed source, and the scoped Opus 5.5 review returned CLEAR using observed Standard service and medium effort.


### Current native and browser qualification

OBSERVED: the fixed-source native harness completed all 24 paired-topic shapes, 23 successful continuations and 24 private receipts, including two separate pre-submission collision refusals before the original URI could be added once. The owning consumer passed 139 assertions, including seven new collision-proof assertions that each rejected a deliberately corrupted proof. The production worker, processor and Calibre path preserved source bytes and 0640 mode, seeding state, the original EPUB, Calibre bookmarks and application reading state. Root independently recreated and parsed all 24 paired-topic and 14 inherited pure-v2 packets with released libtorrent 2.0.11, joining payload digests, exact info bytes, piece layers, original full topics and native IDs. This sessionless join establishes no real daemon or peer transfer.

The first fixed-source full run failed at the unchanged local macOS PDF subprocess with SIGSEGV; the identical command then completed successfully. Both results are retained. The successful host run took about 1,551 seconds and called the full probe directly. It did not exercise the Linux Docker consumer's 1,200-second transport bound; current-head Docker CI remains a publication gate. No assertion or PDF requirement was relaxed.

OBSERVED: the actual French production SPA pilot passed all four root and `/books` cells at 1280 and 375 pixels, with real session/CSRF requests, explicit selection, normal Calibre processing, private receipts and clicked downloads. Root independently recreated each of the four captured hybrid packets and joined both full hashes, the native ID, exact info bytes and piece layers, both payloads and the clicked EPUB download. Root inspected the three French phone choice/receipt/book screenshots and verified that all four server groups and eight fixture ports were closed, owned runtime and temporary directories were removed, and the normal build lease was absent after release. This controlled host fixture explicitly supplies worker availability and advances workers manually; it establishes no unmodified s6 boot or container-health behavior. Public source administration still refuses loopback through the production SSRF boundary; the authorized fixture source was seeded privately. Current-head CI, selected image payloads and fresh main/feedback/serial checks remain delivery gates.
