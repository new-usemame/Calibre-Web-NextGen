# Local gevent search experiment for #2971

On this generated catalog, the median authenticated missing-book GET during a broad search fell from **701.8 ms to 7.0 ms**. The following measurements used real HTTP over loopback to the production application entry point, `cps.py`, with its gevent listener. Each run booted a new server and generated its own SQLite files. The client ran in a separate process in the same network-isolated container.

| Measurement | Upstream route at 04e065dae | Worker enabled |
| --- | ---: | ---: |
| Idle missing-book GET, median of 10 | 3.8 ms | 4.0 ms |
| Missing-book GET during search, median of 10 | 701.8 ms | 7.0 ms |
| Range of those 10 overlap samples | 692.7–722.7 ms | 4.4–10.2 ms |
| Broad search, median of the same 10 | 848.4 ms | 773.7 ms |
| Missing-book GET during a six-search burst, one trial | 5,022.8 ms | 5.5 ms |
| Search responses in the burst | 6 × 200 | 4 × 200, 2 × 503 with Retry-After |

The overlap probe was issued 150 ms after submitting the broad search, following a warm-up. The request was `GET /api/v1/books?search=の&page=1&per_page=50`. A restricted reader searched and a separately authenticated admin requested a known-missing book. Both endpoints used the normal Flask login, visibility and serializer paths. There were 126,000 synthetic CJK titles, one shared author, one denied tag, one hidden book and one searchable comments column. Storage was local to Docker. A separate control run with the flag off on the experimental branch gave a 717.1 ms median overlap latency.

Both versions returned the same ordered first-page IDs and the same complete selection of 62,998 IDs for the restricted reader. The admin saw 63,000 matches. A custom-column-only search returned the expected single book, and selecting more than the supported limit returned HTTP 413. The 14 existing quick-search parity and cost tests also passed.

## Reproduce

Build a development image from this branch, then run the same benchmark with the worker off and on. These commands exercise the current trial defaults. The historical comparison above used the inline route at `04e065dae` and a four-slot worker with immediate rejection at capacity.

```sh
docker build -t cwng-search-dev .
docker run --rm --network none -v "$PWD:/src:ro" -w /src \
  --entrypoint python3 cwng-search-dev scripts/bench_catalog_search_worker.py
docker run --rm --network none -v "$PWD:/src:ro" -w /src -e CWNG_EXPERIMENTAL_SEARCH_WORKER=1 \
  --entrypoint python3 cwng-search-dev scripts/bench_catalog_search_worker.py
```

The final JSON line contains the ordered page IDs, the idle samples and all ten [search, overlap] pairs in milliseconds, plus burst outcomes. The benchmark creates temporary databases and uses the fixture accounts only inside its disposable container.

## Implementation and limits

The route resolves the user's filters and sort on the request greenlet. A single native worker evaluates the count and ordered ID selection in one read transaction on a private SQLite connection with both real databases attached read-only. Reusing the default engine would share the serialized StaticPool connection with the request thread. The worker returns plain IDs. Page hydration, filtering again by the original predicates, and serialization run on the request greenlet. The feature is opt-in and scoped to search on file-backed gevent deployments, for regular pages of 1–500 items and the existing select-all path.

The initial prototype admitted four requests including the active search. Beyond that it returned HTTP 503 with a one-second retry hint. The burst comparison includes this load shedding. The single-search comparison has no rejections. Hydration can still execute parts of the search predicate on the hub.

The refreshed Python impact map reconciled the GET route to `list_books` and resolved its direct calls to `_catalog_book_query`, `_join_sort`, `_selection_response`, and `_rows_to_items` with high confidence. It reported 306 blind spots in the module, led by unknown receivers or attributes (224) and ambiguous attribute names (46), including ORM query chains. It excludes frontend and template consumers. Historical recall ranges from 12.5% on raw co-changes to 80% on curated module-level pairs. The HTTP measurements exercise paths the static map cannot resolve.

## Sustained searches with concurrent writes

`scripts/stress_catalog_search_worker.py` uses the same Docker image, fixture and gevent entry point as the original benchmark. Each container warms the catalog, then runs a 12-second control with writes and unrelated requests only, a minute of six simultaneous searches every six seconds, and a minute of two evenly spaced searches per second. Each phase also sends four authenticated unrelated GETs per second, two authenticated rating PUTs per second to the app, and two independent SQLite commits per second to a custom column in the library. The harness reads back the final rating and searches for the last committed custom-column value after each phase.

The load generator schedules arrivals against the clock regardless of how many searches are pending. It rotates through six pages, checks totals and ordered IDs for each successful search, and checks the response body and retry hint on every 503. It measures latency from the scheduled arrival and waits for outstanding work before changing phases. The unrelated GET targets a missing book by default. Set `STRESS_DETAIL_PROBE=1` to fetch and validate an existing book's full detail response. Set `STRESS_SECONDS` to change the length of both search phases.

```sh
docker run --rm --network none -v "$PWD:/src:ro" -w /src \
  -e CWNG_EXPERIMENTAL_SEARCH_WORKER=1 \
  -e CWNG_EXPERIMENTAL_SEARCH_THREADS=1 -e CWNG_EXPERIMENTAL_SEARCH_ADMISSION=8 \
  -e CWNG_EXPERIMENTAL_SEARCH_SINGLE_SCAN=1 \
  --entrypoint python3 cwng-search-dev scripts/stress_catalog_search_worker.py
```

The worker defaults off. When enabled, the trial defaults are one thread and eight admitted searches, including searches queued for the thread. A request that finds all slots occupied waits cooperatively for up to 250 ms. If no slot opens, it receives HTTP 503 with a one-second retry hint. The measurements below predate this grace period. With the single-scan flag, a default-sort page gets its IDs and exact total from one query using SQLite's window count over distinct visible books. An out-of-range page still computes its exact total. Other sorts and select-all use the original worker queries. Each request reads current database state and there is no cross-request cache.

### Results on the 126,000-book fixture

Each row below includes 240 unrelated GETs, 120 rating updates and 120 library updates. Every one completed with its expected result. The search latency column describes successful searches only. Drain time measures how long outstanding work continued past the scheduled end of the phase. Each configuration ran in its own container, sequentially, on a host with an eight-core Intel i7-9700K.

| Threads / admitted | Query | Search arrivals | Search 503s / sent | Search p95 (s) | GET p95 (ms) | Rating p95 (ms) | Library write p95 (ms) | Drain (s) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 / 4 | Two queries | Bursts | 20 / 60 | 3.33 | 49 | 79 | 32 | 0 |
| 1 / 8 | Two queries | Bursts | 0 / 60 | 4.97 | 37 | 75 | 35 | 0 |
| 2 / 8 | Two queries | Bursts | 6 / 60 | 9.09 | 45 | 71 | 22 | 2.64 |
| 1 / 8 | Single scan | Bursts | 0 / 60 | 2.76 | 52 | 72 | 36 | 0 |
| 1 / 4 | Two queries | 2/s | 44 / 120 | 3.29 | 30 | 29 | 22 | 2.23 |
| 1 / 8 | Two queries | 2/s | 40 / 120 | 6.49 | 28 | 29 | 23 | 5.03 |
| 2 / 8 | Two queries | 2/s | 62 / 120 | 9.74 | 55 | 56 | 22 | 7.52 |
| 1 / 8 | Single scan | 2/s | 0 / 120 | 0.51 | 45 | 64 | 28 | 0 |

With the single scan, the control phase without searches gave p95s of 49 ms for the unrelated GET, 51 ms for the rating PUT and 29 ms for the library update. Under bursts the slowest GET took 125 ms against 52 ms in the control. The wider queue absorbed short bursts, and using one scan brought the sustained service rate up to the offered two searches per second on this catalog.

A fresh container repeated the single-scan test with `STRESS_DETAIL_PROBE=1` and `STRESS_SECONDS=120`. The authenticated GET fetched the full detail for book 2 while that book's rating and library metadata were being updated.

| Phase | Searches completed / sent | Search p95 | Detail GET p95 / max | Rating p95 | Library write p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Control without searches | — | — | 65 / 184 ms | 64 ms | 27 ms |
| Bursts of six | 120 / 120 | 2.87 s | 67 / 308 ms | 76 ms | 37 ms |
| Two per second | 240 / 240 | 0.55 s | 64 / 183 ms | 49 ms | 26 ms |

All 960 detail GETs, 480 rating PUTs and 480 library commits during the two search phases succeeded. Both phases drained by their scheduled end. Burst traffic still increased tail latency, particularly for the slowest detail GET. This measures one broad term in the default sort, on warm local storage with small writes. Concurrent bulk imports, library replacement, network storage and sustained arrival rates above two searches per second remain unmeasured. The worker retains its admission bound to handle overload.

The refreshed impact map at `d17727182` connected the live catalog route to the worker via an exact Python import. It reported 19 unresolved or guessed calls in the worker module and 312 in the route module. In particular, it guessed file locks for gevent semaphore calls. The map covers Python only, with historical recall ranging from 12.5% to 80%. This experiment exercises the route through real HTTP.
