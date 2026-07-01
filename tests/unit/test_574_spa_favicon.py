"""Regression tests for #574 — the new UI had no favicon (blank browser-tab
icon) because the Vite-built shell ships no <link rel="icon">. The Flask-served
shell now injects the app's existing /static/favicon.ico, prefix-aware.
"""
import flask
import pytest

INDEX = (
    '<!doctype html><html><head><title>Calibre-Web NextGen</title>'
    '<script type="module" src="/static/app/assets/index-abc.js"></script>'
    '</head><body><div id="root"></div></body></html>'
)


def _spa_app(monkeypatch, tmp_path):
    import cps.spa as spa_mod
    (tmp_path / "index.html").write_text(INDEX)
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.setenv("CWNG_SPA", "1")
    app = flask.Flask(__name__)
    app.register_blueprint(spa_mod.spa)
    return app


@pytest.mark.unit
def test_favicon_injected_at_root(monkeypatch, tmp_path):
    app = _spa_app(monkeypatch, tmp_path)
    body = app.test_client().get("/app").get_data(as_text=True)
    assert '<link rel="icon" href="/static/favicon.ico">' in body
    assert 'apple-touch-icon' in body
    assert '/static/favicon.ico' in body


@pytest.mark.unit
def test_favicon_prefixed_behind_proxy(monkeypatch, tmp_path):
    """The favicon href must carry the reverse-proxy mount prefix too, else it
    404s behind a subpath (same class of bug as #571)."""
    app = _spa_app(monkeypatch, tmp_path)
    body = app.test_client().get(
        "/app", environ_overrides={"SCRIPT_NAME": "/cwa"}).get_data(as_text=True)
    assert '<link rel="icon" href="/cwa/static/favicon.ico">' in body
    assert 'href="/static/favicon.ico"' not in body


@pytest.mark.unit
def test_favicon_file_present_on_disk():
    """The referenced asset must actually exist (served by Flask's /static)."""
    import os
    import cps
    fav = os.path.join(os.path.dirname(cps.__file__), "static", "favicon.ico")
    assert os.path.isfile(fav)
