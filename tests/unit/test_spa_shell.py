import pytest
import flask


def _spa_app(monkeypatch, tmp_spa_dir=None):
    """Build a Flask app with the spa blueprint. When tmp_spa_dir is given, point
    the shell at it (with a minimal index.html) so the serving logic is tested
    independently of whether the real Vite bundle has been built — the Fast Tests
    CI job runs against source and never builds the frontend (that's the separate
    Frontend Build job + the Docker image build)."""
    import cps.spa as spa_mod
    if tmp_spa_dir is not None:
        (tmp_spa_dir / "index.html").write_text(
            "<!doctype html><title>Calibre-Web NextGen</title><div id=root></div>")
        monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_spa_dir))
    app = flask.Flask(__name__)
    app.register_blueprint(spa_mod.spa)
    return app


@pytest.mark.unit
def test_spa_optout_404(monkeypatch):
    """Explicit opt-out (CWNG_SPA=0) disables /app entirely."""
    monkeypatch.setenv("CWNG_SPA", "0")
    app = _spa_app(monkeypatch)
    assert app.test_client().get("/app").status_code == 404


@pytest.mark.unit
def test_spa_default_on_serves_shell(monkeypatch, tmp_path):
    """Default (CWNG_SPA unset) is ENABLED — every updated instance surfaces the
    new UI without operator config. Serves the shell when the bundle is present."""
    monkeypatch.delenv("CWNG_SPA", raising=False)
    app = _spa_app(monkeypatch, tmp_path)
    resp = app.test_client().get("/app")
    assert resp.status_code == 200
    assert b"NextGen" in resp.data


@pytest.mark.unit
def test_spa_explicit_empty_env_is_optout(monkeypatch):
    """An explicit empty value (CWNG_SPA=) means opt-out — distinct from UNSET,
    which keeps the default-on. (An operator blanking the var means 'off'.)"""
    import cps.spa as spa_mod
    monkeypatch.setenv("CWNG_SPA", "")
    assert spa_mod._spa_enabled() is False
    app = _spa_app(monkeypatch)
    assert app.test_client().get("/app").status_code == 404


@pytest.mark.unit
def test_spa_missing_bundle_404(monkeypatch, tmp_path):
    """Enabled but no build artifact present -> 404 (don't serve a broken shell)."""
    monkeypatch.setenv("CWNG_SPA", "1")
    empty = tmp_path / "empty"
    empty.mkdir()
    import cps.spa as spa_mod
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(empty))
    app = flask.Flask(__name__)
    app.register_blueprint(spa_mod.spa)
    assert app.test_client().get("/app").status_code == 404


@pytest.mark.unit
def test_spa_enabled_serves_shell(monkeypatch, tmp_path):
    monkeypatch.setenv("CWNG_SPA", "1")
    app = _spa_app(monkeypatch, tmp_path)
    resp = app.test_client().get("/app")
    assert resp.status_code == 200
    assert b"NextGen" in resp.data


@pytest.mark.unit
@pytest.mark.parametrize("path", [
    "/app", "/app/", "/app/book/5", "/app/authors",
    # Virtual library. These exist only as client-side routes, so a bookmark or
    # a pasted link reaches them solely because the shell answers their path; a
    # green Vite build proves the bundle compiles, not that the URL resolves.
    "/app/find-books", "/app/admin/acquisition",
])
def test_spa_serves_all_client_routes(monkeypatch, tmp_path, path):
    """The shell must answer /app, /app/ (trailing slash) and any deep client-side
    route, so a hard reload / shared link on any SPA path boots the app instead of
    hitting a server 404. Regression: /app/ previously 404'd because the
    /app/<path:path> rule doesn't match an empty path segment."""
    monkeypatch.setenv("CWNG_SPA", "1")
    app = _spa_app(monkeypatch, tmp_path)
    resp = app.test_client().get(path)
    assert resp.status_code == 200, f"{path} should serve the SPA shell"
    assert b"NextGen" in resp.data


@pytest.mark.unit
def test_spa_context_processor_default_on_with_bundle(monkeypatch, tmp_path):
    """Default-on + bundle present -> cwng_spa_enabled True, and the running
    version is exposed (so the nudge banner can reset its dismissal per update)."""
    import cps.spa as spa_mod
    (tmp_path / "index.html").write_text("x")
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.delenv("CWNG_SPA", raising=False)
    out = spa_mod._inject_spa_flag()
    assert out["cwng_spa_enabled"] is True
    assert "cwng_app_version" in out


@pytest.mark.unit
def test_spa_context_processor_optout_false(monkeypatch, tmp_path):
    """Explicit opt-out -> flag False even with a bundle present."""
    import cps.spa as spa_mod
    (tmp_path / "index.html").write_text("x")
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.setenv("CWNG_SPA", "0")
    assert spa_mod._inject_spa_flag()["cwng_spa_enabled"] is False


@pytest.mark.unit
def test_spa_context_processor_no_bundle_false(monkeypatch, tmp_path):
    """Default-on but no bundle on disk -> flag False (don't nudge toward a 404)."""
    import cps.spa as spa_mod
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(empty))
    monkeypatch.delenv("CWNG_SPA", raising=False)
    assert spa_mod._inject_spa_flag()["cwng_spa_enabled"] is False
