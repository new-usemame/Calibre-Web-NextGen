"""Open-loop mixed workload. Run inside the isolated Docker benchmark container."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import sqlite3
import threading
import time

import requests

from bench_catalog_search_worker import BASE, MISSING, SEARCH, login, run


def p95(samples):
    if not samples:
        return None
    return round(sorted(samples)[math.ceil(0.95 * len(samples)) - 1], 1)


def scenario(root):
    reader = login("reader", "bench-reader")
    admin = login("admin", "admin123")
    token = admin.get(BASE + "/auth/csrf", timeout=10).json()["csrf_token"]
    db_file = root / "library/metadata.db"
    sessions = threading.local()
    sequence = Counter()
    probe_book = os.environ.get("STRESS_DETAIL_PROBE") == "1"
    probe_path = "/books/2" if probe_book else MISSING
    expected = {"search": "200", "probe": "200" if probe_book else "404",
                "rating": "200", "metadata": "200"}
    # Fixture timestamps follow insertion order, with descending ID breaking ties.
    expected_ids = range(125999, 3, -2)

    def validate_page(body, page):
        offset = (page - 1) * 50
        assert body["total"] == len(expected_ids)
        assert [item["id"] for item in body["items"]] == list(expected_ids[offset:offset + 50])

    for page in (1, 2, 3, 4, 5, 6, 1260, 1261):
        response = reader.get(BASE + "/books", params=dict(SEARCH, page=page), timeout=30)
        response.raise_for_status()
        validate_page(response.json(), page)
    missing = reader.get(BASE + "/books", params={"search": "missingfixtureterm"}, timeout=30)
    missing.raise_for_status()
    assert missing.json()["total"] == 0 and missing.json()["items"] == []

    def request(kind, index):
        if kind == "metadata":
            with sqlite3.connect(db_file, timeout=5) as conn:
                changed = conn.execute("UPDATE custom_column_1 SET value=? WHERE book=2",
                                       (f"stressmarker{index}x",)).rowcount
                assert changed == 1
            return 200
        if not hasattr(sessions, "client"):
            sessions.client = requests.Session()
            sessions.client.cookies.update((reader if kind == "search" else admin).cookies)
        client = sessions.client
        if kind == "probe":
            response = client.get(BASE + probe_path, timeout=15)
            assert str(response.status_code) == expected["probe"], response.status_code
            if probe_book:
                assert response.json()["id"] == 2
        elif kind == "rating":
            score = 1 + index % 10
            response = client.put(BASE + "/books/2/rating", json={"rating": score},
                                  headers={"X-CSRFToken": token}, timeout=15)
            assert response.status_code == 200 and response.json()["personal_rating"] == score, response.text[:200]
        else:
            page = 1 + index % 6
            response = client.get(BASE + "/books", params=dict(SEARCH, page=page), timeout=30)
            if response.status_code == 503:
                assert response.headers.get("Retry-After") == "1"
                assert response.json()["error"]["code"] == "search_busy"
            else:
                response.raise_for_status()
                validate_page(response.json(), page)
        return response.status_code

    def sample(kind, index, due):
        began = time.monotonic()
        try:
            status = str(request(kind, index))
        except Exception as exc:
            status = f"{type(exc).__name__}: {exc}"[:150]
        finished = time.monotonic()
        return kind, status, finished, (finished - due) * 1000, (began - due) * 1000

    duration = int(os.environ.get("STRESS_SECONDS", "60"))
    assert duration >= 6
    for phase, seconds in [("control", 12), ("burst", duration), ("overload", duration)]:
        # Per-class executors prevent a full search queue starving the load generator's probes.
        with ThreadPoolExecutor(32) as searches, ThreadPoolExecutor(4) as probes, \
                ThreadPoolExecutor(1) as ratings, ThreadPoolExecutor(1) as metadata:
            jobs = []
            start = time.monotonic()
            for tick in range(seconds * 4):
                due = start + tick / 4
                time.sleep(max(0, due - time.monotonic()))
                arrivals = [(probes, "probe")]
                if tick % 2 == 0:
                    arrivals.extend([(ratings, "rating"), (metadata, "metadata")])
                if phase == "burst" and tick % 24 == 0:
                    arrivals.extend([(searches, "search")] * 6)
                if phase == "overload" and tick % 2 == 0:
                    arrivals.append((searches, "search"))
                for pool, kind in arrivals:
                    sequence[kind] += 1
                    jobs.append(pool.submit(sample, kind, sequence[kind], due))
            rows = [job.result() for job in jobs]
        result = {"phase": phase, "seconds": seconds,
                  "worker": os.environ.get("CWNG_EXPERIMENTAL_SEARCH_WORKER") == "1",
                  "threads": int(os.environ.get("CWNG_EXPERIMENTAL_SEARCH_THREADS", "1")),
                  "admission": int(os.environ.get("CWNG_EXPERIMENTAL_SEARCH_ADMISSION", "8")),
                  "single_scan": os.environ.get("CWNG_EXPERIMENTAL_SEARCH_SINGLE_SCAN") == "1",
                  "probe_path": probe_path}
        deadline = start + seconds
        for kind, success in expected.items():
            group = [row for row in rows if row[0] == kind]
            if not group:
                continue
            good = [row for row in group if row[1] == success]
            result[kind] = {"status": dict(Counter(row[1] for row in group)),
                            "p95_ms": p95([row[3] for row in good]),
                            "max_ms": round(max((row[3] for row in good), default=0), 1),
                            "late_finishes": sum(row[2] > deadline for row in good),
                            "max_dispatch_lag_ms": round(max(row[4] for row in group), 1)}
        result["drain_s"] = round(max(0, max(row[2] for row in rows) - deadline), 2)
        print("STRESS " + json.dumps(result), flush=True)
        assert all(status == expected[kind] or (kind == "search" and status == "503")
                   for kind, status, *_ in rows), result
        assert admin.get(BASE + "/books/2/rating", timeout=10).json()["personal_rating"] == 1 + sequence["rating"] % 10
        marker = f"stressmarker{sequence['metadata']}x"
        result_books = reader.get(BASE + "/books", params={"search": marker}, timeout=30).json()
        assert result_books["total"] == 1 and result_books["items"][0]["id"] == 2


if __name__ == "__main__":
    run(scenario)
