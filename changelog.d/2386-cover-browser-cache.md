### Fixed

- **Covers load from the browser cache again instead of re-downloading on every visit (#2386).** Since the per-user library change, every cover was sent with `Cache-Control: private, no-store`, so opening the library fetched every thumbnail again. Covers whose URL carries their current version are cached by the browser again; covers that are still being generated, placeholder covers and catalog data keep revalidating. Thanks @Dirk71 for the network capture that pinned it down.
