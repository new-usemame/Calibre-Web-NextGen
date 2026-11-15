# General cache storage under config (#995)

The historical metadata logs/temp portions of #995 are already delivered. The remaining general cache default still resolved to the application tree. It now uses CONFIG_DIR/cache, honoring the existing CALIBRE_DBPATH config location; an explicit CACHE_DIR is preserved. Existing thumbnails stay in CONFIG_DIR/thumbnails.

Product decision: general cache entries are disposable derived files and can regenerate. Do not migrate or delete the former application-tree cache; that avoids treating cache contents as durable user data or adding a startup migration. Metadata-log migration is separate and already exists. Remove obsolete app-tree cache mkdir/chown from cwa-init; normal config ownership and network-share behavior are retained.

A real-module subprocess test loads path resolution and FileSystem without starting the application factory, writes/reads/deletes a cache file, and checks persisted thumbnail bytes survive. Default-path case was seen red before the change; explicit-override case passed before/after. Focused cache/metadata/ownership/preview checks: 75 passed, one skipped. Full local smoke/unit: 10,274 passed, 103 skipped, only the two reproduced baseline metadata-replacement SQLite I/O failures. Independent read-only review found no source blocker.

Actual complete private image 399f02785c185fec59aae7fc27d676bc3f64d8c3feb637d0318179fb31d72ba2 booted healthy. Executing the real FileSystem as runtime UID 501 proved default /config/cache and explicit /config/cwng995-explicit-cache roundtrip, file ownership and deletion; owned probes were cleaned. Thumbnail resolution stayed /config/thumbnails. Authenticated HTTP GET /cover/189/sm returned 200 image/webp, 13,508 bytes, SHA256 83aba95226da4aaa075a0f6da9bfd1541403a270cf8a2b16349a585eefbaeb5b, matching the existing book_189_r1.webp on that private volume. This establishes thumbnail-route preservation without changing real cover bytes. No physical device or production-volume claim.

Logs contain known invalid private-seed EPUB/backfill errors (not ZIP files), with no cache-permission failure. These are not claimed fixed. Evidence is retained in the local X8 cwng-promised-995 directory: seen-red/focused/full logs, container default/explicit cache IO, HTTP thumbnail response/hash and matching files, container log and subject identity. No product UI changed, so screenshots add no relevant evidence.

Shell syntax and diff checks pass. cwa-init may need rebasing after the separate #947 startup change; neither request depends on bundling the other. Current-head verification governs merging; releases remain separately owned.


## Serial merge integration (2026-10-02)

Rebased onto current main d98944a6e after non-root initialization #2408. The cwa-init conflict removes the entire obsolete root-only app-cache creation wrapper; cache now belongs under writable config or explicit CACHE_DIR. Root/non-root config ownership behavior and all #2408 ImageMagick policy, kepubify guard, runtime-directory fail-fast helper, AutoLibrary no-op/override and Qt marker logic survive. The ownership comment now describes config instead of the removed app-tree cache. Constants, ownership script and cache behavioral test bytes are unchanged from the independently reviewed original; all preceding CHANGES rows and SPA anchors survive. Other-owner #2403 remains untouched.

Current actual FileSystem/cache/metadata/ownership/non-root/runtime/preview/classifier packet104 passed,one existing skip. Shell syntax and diff checks pass. Measured classifier closure214 of272 matches the README. Original actual runtime-user default/explicit cache I/O and authenticated thumbnail byte-preservation proof remain applicable; fresh exact-head CI and an independent bounded startup/cache integration read govern landing.
