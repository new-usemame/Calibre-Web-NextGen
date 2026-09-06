"""Explicitly invoked in isolation by test_real_app.py."""
import pytest

pytestmark = pytest.mark.integration


def test_second_app_preserves_live_runtime(real_app):
    import sys
    import threading
    import _thread
    import cps
    from cps import services
    from cps.services.background_scheduler import BackgroundScheduler

    updater = cps.updater_thread
    scheduler = BackgroundScheduler._instance
    assert scheduler is not None
    assert updater.is_alive()
    assert scheduler.scheduler.running
    scheduler_thread = scheduler.scheduler._thread
    assert scheduler_thread.is_alive()
    assert isinstance(cps._process_runtime_lock, _thread.RLock)
    assert not cps._process_runtime_lock._is_owned()
    # Nonblocking acquisition from a different OS thread checks ownership and
    # release without risking a deadlock or invoking the factory off-thread.
    def foreign_acquire():
        acquired = cps._process_runtime_lock.acquire(blocking=False)
        acquisitions.append(acquired)
        if acquired:
            cps._process_runtime_lock.release()

    acquisitions = []
    with cps._process_runtime_lock:
        probe = threading.Thread(target=foreign_acquire)
        probe.start()
        probe.join(timeout=2)
        assert not probe.is_alive()
    assert acquisitions == [False]

    ownership = []
    def profile(frame, event, arg):
        if event == "call" and frame.f_code is cps.create_app.__wrapped__.__code__:
            ownership.append(cps._process_runtime_lock._is_owned())

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        second = cps.create_app(cps.config, services)
    finally:
        sys.setprofile(previous)
    assert second is not real_app
    assert ownership == [True]
    assert cps.updater_thread is updater and updater.is_alive()
    assert BackgroundScheduler._instance is scheduler
    assert scheduler.scheduler.running
    assert scheduler.scheduler._thread is scheduler_thread
    assert scheduler_thread.is_alive()
    assert sum(t is updater for t in threading.enumerate()) == 1
    assert sum(isinstance(t, type(updater)) for t in threading.enumerate()) == 1
    assert sum(t.name == scheduler_thread.name for t in threading.enumerate()) == 1
    assert not cps._process_runtime_lock._is_owned()
    probe = threading.Thread(target=foreign_acquire)
    probe.start()
    probe.join(timeout=2)
    assert not probe.is_alive()
    assert acquisitions == [False, True]
    print("LIFECYCLE second: updater_alive=True updater_count=1 scheduler_running=True "
          "scheduler_thread_count=1 same_runtime=True factory_lock_owned=True "
          "foreign_acquire_held=False foreign_acquire_after=True")



def test_real_bootstrap(real_app):
    from cps.reverseproxy import ReverseProxied

    import cps

    assert cps.config.db_configured
    assert real_app.blueprints
    assert isinstance(real_app.wsgi_app, ReverseProxied)
    assert "csrf" in real_app.extensions
    assert real_app.error_handler_spec
    response = real_app.test_client().get("/login")
    assert response.status_code == 200
    assert b"csrf_token" in response.data


def blueprint_probes(app):
    """Choose a real rule per blueprint; fail closed on unsupported converters."""
    from uuid import UUID
    from werkzeug.routing import IntegerConverter, FloatConverter, UUIDConverter

    adapter = app.url_map.bind("localhost")
    for name in app.blueprints:
        rules = [r for r in app.url_map.iter_rules()
                 if r.endpoint.rsplit(".", 1)[0] == name]
        if not rules:
            assert name == "jinjia", f"New routeless blueprint needs a behavioral check: {name}"
            assert app.jinja_env.filters["shortentitle"]("fixture", 20) == "fixture"
            continue
        candidates = sorted(rules, key=lambda r: (
            "GET" not in r.methods, not r.endpoint.endswith(".authorized"),
            len(r.arguments), r.rule))
        for rule in candidates:
            values = dict(rule.defaults or {})
            for key in rule.arguments - values.keys():
                converter = rule._converters[key]
                if isinstance(converter, IntegerConverter):
                    values[key] = 1
                elif isinstance(converter, FloatConverter):
                    values[key] = 1.0
                elif isinstance(converter, UUIDConverter):
                    values[key] = UUID(int=1)
                else:
                    values[key] = "fixture"
            method = "GET" if "GET" in rule.methods else "POST"
            path = adapter.build(rule.endpoint, values, method=method)
            endpoint, _ = adapter.match(path, method=method)
            if endpoint == rule.endpoint:
                yield name, method, path
                break
        else:
            pytest.fail(f"No reachable rule for {name}")


def test_blueprint_requests(real_app):
    import json

    rows = []
    for name, method, path in blueprint_probes(real_app):
        response = real_app.test_client().open(path, method=method)
        assert response.status_code < 500, (name, method, path, response.status_code)
        row = {"blueprint": name, "method": method, "path": path,
               "status": response.status_code, "mimetype": response.mimetype,
               "location": response.headers.get("Location"),
               "json": response.get_json(silent=True)}
        # Werkzeug's test client presents REMOTE_ADDR 127.0.0.1; a request through
        # a container's published port arrives from the bridge gateway. A route that
        # reads the client address therefore answers differently through the two
        # probes for a reason that is not a difference in the application. Ask each
        # route directly, from a documentation-range address (RFC 5737), so the
        # Docker comparison can exclude exactly the routes that demonstrably care
        # rather than a hand-maintained list. No assertion here: a route is entitled
        # to refuse a stranger, and this probe only classifies.
        stranger = real_app.test_client().open(
            path, method=method, environ_base={"REMOTE_ADDR": "192.0.2.10"})
        row["client_address_sensitive"] = (
            stranger.status_code != row["status"]
            or stranger.mimetype != row["mimetype"]
            or stranger.headers.get("Location") != row["location"]
            or stranger.get_json(silent=True) != row["json"])
        rows.append(row)
        print("BLUEPRINT", name, method, path, response.status_code, response.mimetype)
    assert len(rows) == len(real_app.blueprints) - 1

    # Comparing the app to its own enumeration cannot notice a blueprint that
    # stopped being registered: the loop simply gets shorter and every remaining
    # answer still holds. MEASURED -- deleting the OPDS registration outright left
    # this suite green before this check existed. The committed list is the only
    # thing here that does not come from the app itself.
    import pathlib
    pinned = json.loads(pathlib.Path(__file__).with_name("blueprints.json").read_text())
    expected = pinned["always"]
    # Conditional blueprints are registered only when their condition holds, and
    # this fixture boots with the shipped defaults. Their absence is not an error
    # and their presence is not an error -- they are listed so the gap is visible
    # rather than silent, and so an unexpected NEW blueprint still fails below.
    conditional = [name for names in pinned["conditional"].values() for name in names]
    actual = sorted(real_app.blueprints)
    missing = [name for name in expected if name not in actual]
    added = [name for name in actual if name not in expected and name not in conditional]
    assert not missing, (
        "blueprint(s) no longer registered: %s. If the removal is intended, delete "
        "them from tests/integration/real_app/blueprints.json in the same commit."
        % missing)
    assert not added, (
        "new blueprint(s) not in the pinned list: %s. Add them to "
        "tests/integration/real_app/blueprints.json so a later disappearance is a diff."
        % added)

    print("REAL_APP_WIRE=" + json.dumps(rows, sort_keys=True))


def test_registration_is_load_bearing(real_app):
    """A bare factory has no login route; registration must change the wire."""
    import cps
    from cps import services
    from cps.main import register_blueprints

    bare = cps.create_app(cps.config, services)
    assert bare.test_client().get("/login").status_code == 404
    # Flask forbids registering after the first request: use another fresh app.
    registered = cps.create_app(cps.config, services)
    register_blueprints(registered)
    response = registered.test_client().get("/login")
    assert response.status_code == 200
    assert b"csrf_token" in response.data
    assert registered.test_client().post("/login", data={}).status_code == 400
    assert real_app.test_client().post("/login", data={}).status_code == 400


def test_native_lock_stalls_greenlets(real_app):
    """Characterize the forward constraint in this disposable process only."""
    import threading
    import gevent
    from gevent import monkey
    import cps

    assert not monkey.is_module_patched("threading")
    held = threading.Event()
    release = threading.Event()
    events = []

    def owner():
        with cps._process_runtime_lock:
            held.set()
            # Bounded OS wait guarantees release even if the hub is blocked.
            release.wait(timeout=0.2)
            events.append("os_release")

    thread = threading.Thread(target=owner)
    thread.start()
    assert held.wait(timeout=2)

    def contender():
        events.append("lock_wait")
        with cps._process_runtime_lock:
            events.append("lock_acquired")

    def observer():
        events.append("greenlet_ran")
        release.set()

    jobs = [gevent.spawn(contender), gevent.spawn(observer)]
    gevent.joinall(jobs, timeout=3, raise_error=True)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert all(job.ready() for job in jobs)
    assert events == ["lock_wait", "os_release", "lock_acquired", "greenlet_ran"]
    print("LOCK native unpatched: " + " -> ".join(events))
