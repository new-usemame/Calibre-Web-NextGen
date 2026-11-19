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

The local runtime image was `calibre-web-nextgen:metadata-date-dev` (linux/amd64 image ID `7897679d921c88272d8d1a11a4401fabaa67a104b48561cac4d9e5cd02fe3c18`). The worktree supplies the application source. From this branch, run the two measurements sequentially:

```sh
baseline_dir=$(mktemp -d)
git show 04e065dae:cps/api/books.py > "$baseline_dir/books.py"
docker run --rm --network none -v "$PWD:/src:ro" -v "$baseline_dir/books.py:/src/cps/api/books.py:ro" -w /src \
  --entrypoint python3 calibre-web-nextgen:metadata-date-dev scripts/bench_catalog_search_worker.py
docker run --rm --network none -v "$PWD:/src:ro" -w /src -e CWNG_EXPERIMENTAL_SEARCH_WORKER=1 \
  --entrypoint python3 calibre-web-nextgen:metadata-date-dev scripts/bench_catalog_search_worker.py
```

The final JSON line contains the ordered page IDs, the idle samples and all ten [search, overlap] pairs in milliseconds, plus burst outcomes. The benchmark creates temporary databases and uses the fixture accounts only inside its disposable container.

## Implementation and limits

The route resolves the user's filters and sort on the request greenlet. A single native worker evaluates the count and ordered ID selection in one read transaction on a private SQLite connection with both real databases attached read-only. Reusing the default engine would share the serialized StaticPool connection with the request thread. The worker returns plain IDs. Page hydration, filtering again by the original predicates, and serialization run on the request greenlet. The feature is opt-in and scoped to search on file-backed gevent deployments, for regular pages of 1–500 items and the existing select-all path.

The prototype admits four requests including the active search. Beyond that it returns HTTP 503 with a one-second retry hint. The burst comparison includes this load shedding. The single-search comparison has no rejections. The sample is small, with warm local storage, one search term and synthetic metadata. Sustained mixed traffic, concurrent writes or library replacement, network-backed storage, other sort and custom-column distributions, and client handling of overload remain unverified. In particular, hydration can still execute parts of the search predicate on the hub.

The refreshed Python impact map reconciled the GET route to `list_books` and resolved its direct calls to `_catalog_book_query`, `_join_sort`, `_selection_response`, and `_rows_to_items` with high confidence. It reported 306 blind spots in the module, led by unknown receivers or attributes (224) and ambiguous attribute names (46), including ORM query chains. It excludes frontend and template consumers. Historical recall ranges from 12.5% on raw co-changes to 80% on curated module-level pairs. The HTTP measurements exercise paths the static map cannot resolve.
