"""Explicitly invoked in isolation by test_real_app.py."""
import pytest

pytestmark = pytest.mark.integration


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
            assert "shortentitle" in app.jinja_env.filters
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
        rows.append(row)
        print("BLUEPRINT", name, method, path, response.status_code, response.mimetype)
    assert len(rows) == len(real_app.blueprints) - 1
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
