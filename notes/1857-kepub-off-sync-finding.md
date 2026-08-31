# Issue #1857: KEPUB-off Kobo sync finding

This note records the second half of #1857 for a separate dispatch. It is an
investigation result, not a fix in the session-containment branch.

## Observed

- `cps/kobo.py:711` calls `calibre_db.reconnect_db(config, ub.app_DB_path)` on
  every `/v1/library/sync` request. The call is unconditional, outside an error
  boundary, and independent of `config_kobo_prefer_kepub`.
- `cps/db.py:2211-2231` implements `reconnect_db()` by calling `dispose()`,
  disposing the class-level engine, then rebuilding the database setup.
  `cps/db.py:2176-2195` shows that `dispose()` closes sessions/bindings, and
  `cps/db.py:1296-1316` shows setup can return without producing a new factory
  when the library path or `metadata.db` is unavailable.
- `cps/kobo.py:1914-1924` is the complete KEPUB-preference branch in Kobo
  metadata generation. Turning the preference off changes only an EPUB row's
  deferred download from KEPUB to EPUB/EPUB3. A stored KEPUB row remains first
  choice even when the preference is off.
- Existing test policy already names the engine-disposal race:
  `tests/unit/test_metadata_db_write_coordination.py:79-114` forbids the
  post-ingest reconnect endpoint from using the heavy reconnect path because it
  races with active requests. The Kobo sync route still uses that path.

## Finding for the follow-up dispatch

There is a concrete abort mechanism for the reporter's device-side “Sync
failed” with no Kobo-specific terminal log: the unconditional reconnect at
`cps/kobo.py:711` can raise or leave database setup unavailable before the
route reaches its response generation, and there is no local exception handler
to turn that into a diagnosed Kobo response/log entry. The same global engine
disposal also explains how a concurrent sync can invalidate the KEPUB backfill
while it is fetching a book.

The code does **not** establish that switching the KEPUB preference off causes
the reconnect failure; the reconnect runs in both preference states. Treat the
reported toggle as correlation until a request-level reproduction proves a
causal link. The follow-up should capture the failing HTTP status and exception,
then evaluate replacing the per-sync destructive reconnect with the existing
non-disposing refresh model rather than changing KEPUB metadata blindly.
