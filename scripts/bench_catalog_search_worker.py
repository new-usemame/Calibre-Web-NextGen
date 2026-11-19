"""Run from the repository root inside an isolated CWNG container."""
import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import requests

BASE = "http://127.0.0.1:8083/api/v1"
SEARCH = {"search": "の", "page": 1, "per_page": 50}
MISSING = "/books/999999999"


def seed(root):
    with sqlite3.connect(root / "library/metadata.db") as conn:
        conn.executescript(Path("scripts/metadata.db.sql").read_text())
        conn.create_function("title_sort", 1, str)
        conn.create_function("uuid4", 0, lambda: os.urandom(16).hex())
        conn.execute("INSERT INTO authors(id,name,sort) VALUES(1,'著者','著者')")
        conn.executemany("INSERT INTO books(id,title,path) VALUES(?,?,?)", (
            (i, ("日本の文学 " if i % 2 else "春と桜 ") + str(i), str(i))
            for i in range(1, 126001)))
        conn.execute("INSERT INTO books_authors_link(book,author) SELECT id,1 FROM books")
        conn.execute("INSERT INTO tags(id,name) VALUES(1,'restricted')")
        conn.execute("INSERT INTO books_tags_link(book,tag) VALUES(1,1)")
        conn.execute("""INSERT INTO custom_columns(id,label,name,datatype,is_multiple,normalized)
                        VALUES(1,'notes','Notes','comments',0,0)""")
        conn.execute("CREATE TABLE custom_column_1(id INTEGER PRIMARY KEY, book INTEGER UNIQUE, value TEXT)")
        conn.execute("INSERT INTO custom_column_1(book,value) VALUES(2,'unique-benchmark-sentinel')")
    sys.argv = ["cps", "-p", str(root / "config/app.db")]
    import cps
    cps.ub.init_db(sys.argv[-1])
    key, error = cps.config_sql.get_encryption_key(str(root / "config"))
    assert not error, error
    cps.config_sql.load_configuration(cps.ub.session, key)
    settings = cps.ub.session.query(cps.config_sql._Settings).one()
    settings.config_calibre_dir = str(root / "library")
    from werkzeug.security import generate_password_hash
    user = cps.ub.User(name="reader", password=generate_password_hash("bench-reader"),
                       denied_tags="restricted", role=0)
    cps.ub.session.add(user)
    cps.ub.session.flush()
    cps.ub.session.add(cps.ub.UserHiddenBook(user_id=user.id, book_id=3))
    cps.ub.session.commit()


def login(username, password):
    client = requests.Session()
    token = client.get(BASE + "/auth/csrf", timeout=10).json()["csrf_token"]
    response = client.post(BASE + "/auth/login", json={"username": username, "password": password},
                           headers={"X-CSRFToken": token}, timeout=10)
    response.raise_for_status()
    return client


def timed(client, path, params=None, expected=200):
    start = time.monotonic()
    response = client.get(BASE + path, params=params, timeout=60)
    duration = time.monotonic() - start
    assert response.status_code == expected, (response.status_code, response.text[:400])
    return round(duration * 1000, 1), response.json()


def measure():
    reader, probe = login("reader", "bench-reader"), login("admin", "admin123")
    assert timed(probe, "/books", SEARCH)[1]["total"] == 63000
    _, custom = timed(reader, "/books", {"search": "unique-benchmark-sentinel"})
    assert custom["total"] == 1 and custom["items"][0]["id"] == 2
    _, selected = timed(reader, "/books", dict(SEARCH, select_all=1))
    assert selected["total"] == 62998 and set(selected["ids"]) == set(range(5, 126001, 2))
    _, too_large = timed(reader, "/books", {"search": "%", "select_all": 1}, expected=413)
    assert too_large["error"]["code"] == "selection_too_large"
    _, reference = timed(reader, "/books", SEARCH)
    ids = [item["id"] for item in reference["items"]]
    assert reference["total"] == 62998 and len(ids) == 50, reference["total"]
    idle = [timed(probe, MISSING, expected=404)[0] for _ in range(10)]
    pairs = []
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        for _ in range(10):
            future = pool.submit(timed, reader, "/books", SEARCH)
            time.sleep(0.150)
            assert not future.done(), "search finished before overlap probe"
            latency, _ = timed(probe, MISSING, expected=404)
            duration, page = future.result()
            assert page["total"] == reference["total"] and [i["id"] for i in page["items"]] == ids
            pairs.append([duration, latency])
    start_burst = threading.Barrier(7)
    def burst_search():
        with requests.Session() as client:
            client.cookies.update(reader.cookies)
            start_burst.wait(timeout=10)
            response = client.get(BASE + "/books", params=SEARCH, timeout=60)
            if response.status_code == 200:
                assert response.json()["total"] == reference["total"]
            else:
                assert response.status_code == 503 and response.headers["Retry-After"] == "1"
            return response.status_code
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        jobs = [pool.submit(burst_search) for _ in range(6)]
        start_burst.wait(timeout=10)
        time.sleep(0.150)
        burst_probe = timed(probe, MISSING, expected=404)[0]
        statuses = sorted(job.result() for job in jobs)
    print(json.dumps({"total": reference["total"], "ordered_ids": ids,
                      "idle_ms": idle, "search_and_overlap_ms": pairs,
                      "burst_statuses": statuses, "burst_probe_ms": burst_probe}), flush=True)


def run(scenario=None):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for name in ("config", "library", "ingest", "conversion"):
            (root / name).mkdir()
        env = dict(os.environ, CALIBRE_DBPATH=str(root / "config"), CWA_DB_PATH=str(root / "config"),
                   CWA_CALIBRE_LIBRARY_DIR=str(root / "library"), CWA_INGEST_FOLDER=str(root / "ingest"),
                   CWA_TMP_CONVERSION_DIR=str(root / "conversion"), PYTHONPATH=f"{Path.cwd()}:{Path.cwd()}/scripts")
        subprocess.run([sys.executable, __file__, "seed", temp], check=True, env=env)
        log = root / "server.log"
        with log.open("w") as output:
            server = subprocess.Popen([sys.executable, "cps.py", "-p", str(root / "config/app.db")],
                                      env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                for _ in range(120):
                    if server.poll() is not None:
                        raise RuntimeError("server exited")
                    try:
                        if requests.get(BASE + "/auth/csrf", timeout=1).ok:
                            break
                    except requests.ConnectionError:
                        pass
                    time.sleep(0.25)
                assert "Gevent server" in log.read_text(), "expected gevent listener"
                if scenario is None:
                    measure()
                else:
                    scenario(root)
            except Exception:
                print(log.read_text()[-4000:], file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    if sys.argv[1:2] == ["seed"]:
        seed(Path(sys.argv[2]))
    else:
        run()
