# Issue #2201: executed multi-session delivery comparison

Current main at `9ce113de64c418887d19ab084fef56760625fe5e` delivers the
296-book reproduction completely. This change adds coverage, not a runtime fix.

## Reproduction

`tests/unit/test_2201_kobo_multipage_drain.py` drives the real sync handler
through Flask HTTP dispatch. Each of eight sessions sends the preceding
response's opaque sync token. The test never acknowledges a pending page by
calling ledger helpers: the handler consumes the token and updates delivery
state itself.

The fixture uses real SQLAlchemy models and SQLite queries. All 296 books have
both EPUB and KEPUB rows, the production page limit stays at 100, and all books
share a last-modified second and creation time. Four cases cross sync-all and
shelf-only scope with uniform or mixed literal SQLite timestamp text:

```
2026-01-01 00:00:00
2026-01-01 00:00:00.000000
2026-01-01 00:00:00+00:00
2026-01-01 00:00:00.000000+00:00
```

Every book also belongs to a sync-enabled shelf with a newer membership date,
2026-02-01. That date deliberately exercises a cursor advanced beyond the
undelivered books. UUIDs are counted from emitted entitlements, not inferred
from database counts. First delivery must be NewEntitlement, and subsequent
empty sessions must stay empty. Local pages intentionally omit the continuation
header so firmware can persist their tokens between sessions (#1634).

## Observed results

| Code | Sync-all sessions | Shelf-only sessions |
| --- | --- | --- |
| v4.1.43 (`bdead55920e066ff529da0865c712b5dcdf7cfd3`) | 100 New, 100 Changed, 96 Changed, then five empty | 100 New, then seven empty |
| `0e3b86005a736f467fb16db68f79acfe2f318100` | 100 New, 100 New, 96 New, then five empty | Same |
| Current main | 100 New, 100 New, 96 New, then five empty | Same |

Uniform and mixed timestamp text produced identical results. All responses
were HTTP 200. No existing Kobo test was edited, deleted, or weakened.

The v4.1.43 failure output includes these two distinct assertions (each appears
for both timestamp shapes):

```
E   AssertionError: Only 100/296 books received NewEntitlement; changed before new=196; pages=[(100, 0, None), (0, 100, None), (0, 96, None), (0, 0, None), (0, 0, None), (0, 0, None), (0, 0, None), (0, 0, None)]
E   AssertionError: Only 100/296 books received NewEntitlement; changed before new=0; pages=[(100, 0, None), (0, 0, None), (0, 0, None), (0, 0, None), (0, 0, None), (0, 0, None), (0, 0, None), (0, 0, None)]
```

The closing commit is **0e3b86005a, Fix Kobo new-vs-changed classification
across sync pages (#2025)**. Its two relevant changes explain the measurement:

- Classification uses the requesting device's delivery ledger, replacing a
  creation-time watermark that labeled page two's never-delivered books Changed.
- A missing device-ledger entry admits a book independently of the timestamp
  cursor, recovering books excluded after the newer shelf membership date was
  folded into page one's cursor.

This was checked by executing the same four cases on that commit itself, not
only by reading its diff. Both normalized timestamp ordering and format-join
deduplication already exist in the tested tag. Neither needs another fix for
this reproduction.

## Verification and historical harness adaptation

Executed pytest invocations used the existing repository virtualenv, with
isolated CALIBRE_DBPATH and CWNG_PYTEST_TMP_BASE directories. Historical runs
used a detached worktree on the external scratch volume. Test selection and
verbatim final result lines:

```
python -m pytest -q -s tests/unit/test_2201_kobo_multipage_drain.py
======================= 4 passed, 1232 warnings in 5.59s =======================

# v4.1.43, then 0e3b86005a, in the historical worktree:
python -m pytest -q -s -p comparison_adapter tests/unit/test_2201_kobo_multipage_drain.py
======================= 4 failed, 1960 warnings in 4.50s =======================
======================= 4 passed, 1960 warnings in 5.06s =======================

python -m pytest -q tests/unit/*kobo*.py
=============== 1071 passed, 1 skipped, 15566 warnings in 45.00s ===============
```

The commands above show test selection; executable and scratch environment paths
are omitted. The handoff report records the full invocations. The skipped test
requires CWNG_REAL_KOBO_DB to point to an actual KoboReader.sqlite copy.

The historical worktree received unchanged copies of the new test and its
`test_kobo_replay_topup_pagination.py` population helper. Historical production
files stayed unchanged. Its existing #1925 fixture exposed a request-context
helper and a non-callable WSGI placeholder rather than a dispatchable route.
The temporary comparison plugin adapted only that boundary:

```python
import pytest
from flask import Flask, g


@pytest.fixture(autouse=True)
def dispatch_tag_sync(sync_harness):
    from cps import kobo

    class ProxiedWsgi:
        is_proxied = True

        def __call__(self, environ, start_response):
            return Flask.wsgi_app(sync_harness.app, environ, start_response)

    sync_harness.app.wsgi_app = ProxiedWsgi()

    def dispatched_sync():
        g.annotation_origin_device_id = sync_harness.device.id
        return kobo.HandleSyncRequest.__wrapped__()

    sync_harness.app.add_url_rule(
        '/v1/library/sync', view_func=dispatched_sync,
    )
```

During construction, three preliminary runs failed because the new fixture
omitted the shelf relationship, addressed the wrong attached database, and
incorrectly treated absence of continuation as proof that the whole library
was exhausted. These were test-development errors, not product regressions.
The historical failures reported above occur with the corrected test that
passes on main.

## Limits

There is no physical Kobo, NFS mount, or ARM64 host in this verification.
Authentication/device registration is represented by the existing test fixture;
permission filtering, filesystem layout/download URLs, external integrations,
and collection serialization are stubbed at its established boundaries.
The real entitlement serialization, candidate selection, pagination, token
encoding/decoding, and delivery acknowledgment run against SQLite.

This proves complete entitlement announcement across successive sessions for
the tested shapes. It does not prove firmware downloads/imports the EPUB/KEPUB
bytes, real device handling of an unknown ChangedEntitlement, timing or locking
on NFS, ARM64 runtime behavior, proxy-store interactions, or recovery from the
reporter's actual existing device/database state. No production deployment or
GitHub operation was performed.
