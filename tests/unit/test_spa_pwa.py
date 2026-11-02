# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installable web app: manifest, service worker and shell tags (cps/spa.py).

The worker and manifest are served by Flask, not the Vite bundle, because they
must carry the reverse-proxy mount prefix and the worker's scope must cover
<prefix>/app. These tests run against a fake bundle in tmp_path, like
test_spa_shell.py, so they don't depend on a frontend build.
"""
import json

import flask
import pytest


def _app(monkeypatch, tmp_path, assets=("index-abc.js", "Reader-def.js", "index-123.css")):
    import cps.spa as spa_mod
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>Calibre-Web NextGen</title></head>"
        "<body><div id=root></div>"
        '<script type="module" src="/static/app/assets/index-abc.js"></script></body></html>')
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    for name in assets:
        (assets_dir / name).write_text("x")
    (assets_dir / "index-abc.js.map").write_text("{}")
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.delenv("CWNG_SPA", raising=False)
    monkeypatch.delenv("CWNG_PWA", raising=False)
    app = flask.Flask(__name__)
    app.register_blueprint(spa_mod.spa)
    return app


def _sw_config(body):
    first_line = body.split("\n", 1)[0]
    assert first_line.startswith("self.__CWNG_SW__ = ")
    return json.loads(first_line[len("self.__CWNG_SW__ = "):].rstrip(";"))


@pytest.mark.unit
def test_shell_links_manifest_and_flags_pwa(monkeypatch, tmp_path):
    resp = _app(monkeypatch, tmp_path).test_client().get("/app/")
    html = resp.get_data(as_text=True)
    assert '<link rel="manifest" href="/app/manifest.webmanifest">' in html
    assert '<meta name="theme-color"' in html
    assert "window.__CWNG_PWA__=true" in html


@pytest.mark.unit
def test_manifest_is_installable_in_firefox(monkeypatch, tmp_path):
    """Firefox for Android offers Install only for a secure page whose manifest
    has display != browser and an 'any'/'maskable' icon of at least 192px
    (android-components feature-pwa, SessionState.installableManifest)."""
    resp = _app(monkeypatch, tmp_path).test_client().get("/app/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.mimetype == "application/manifest+json"
    assert resp.headers["Cache-Control"] == "no-cache"
    manifest = json.loads(resp.data)
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/app/"
    assert manifest["scope"] == "/app/"
    assert manifest["name"]
    large = [icon for icon in manifest["icons"]
             if "any" in icon["purpose"].split()
             and min(int(n) for n in icon["sizes"].split("x")) >= 192]
    assert large


@pytest.mark.unit
def test_manifest_icons_exist_on_disk():
    import os
    import cps.spa as spa_mod
    static = os.path.join(os.path.dirname(spa_mod.__file__), "static", "img")
    for size in spa_mod._PWA_ICON_SIZES:
        assert os.path.isfile(os.path.join(static, "pwa-icon-%d.png" % size))


@pytest.mark.unit
def test_service_worker_precaches_bundle_without_source_maps(monkeypatch, tmp_path):
    resp = _app(monkeypatch, tmp_path).test_client().get("/app/sw.js")
    assert resp.status_code == 200
    assert resp.mimetype == "text/javascript"
    assert resp.headers["Cache-Control"] == "no-cache"
    body = resp.get_data(as_text=True)
    config = _sw_config(body)
    assert config["prefix"] == ""
    assert config["precache"] == [
        "/static/app/assets/Reader-def.js",
        "/static/app/assets/index-123.css",
        "/static/app/assets/index-abc.js",
    ]
    assert len(config["build"]) == 16
    assert "addEventListener('fetch'" in body


@pytest.mark.unit
def test_build_id_changes_with_bundle(monkeypatch, tmp_path):
    """A new build must change the worker bytes, or browsers keep the old one."""
    builds = []
    for name, assets in (("a", ("index-abc.js",)), ("b", ("index-zzz.js",))):
        bundle = tmp_path / name
        bundle.mkdir()
        body = _app(monkeypatch, bundle, assets=assets).test_client().get("/app/sw.js")
        builds.append(_sw_config(body.get_data(as_text=True))["build"])
    assert builds[0] != builds[1]


@pytest.mark.unit
def test_everything_carries_the_mount_prefix(monkeypatch, tmp_path):
    client = _app(monkeypatch, tmp_path).test_client()
    env = {"SCRIPT_NAME": "/books"}  # as ReverseProxied sets it
    html = client.get("/app/", environ_overrides=env).get_data(as_text=True)
    assert 'href="/books/app/manifest.webmanifest"' in html
    manifest = json.loads(client.get("/app/manifest.webmanifest", environ_overrides=env).data)
    assert manifest["start_url"] == "/books/app/"
    assert manifest["scope"] == "/books/app/"
    assert all(icon["src"].startswith("/books/static/img/") for icon in manifest["icons"])
    config = _sw_config(client.get("/app/sw.js", environ_overrides=env).get_data(as_text=True))
    assert config["prefix"] == "/books"
    assert all(url.startswith("/books/static/app/assets/") for url in config["precache"])


@pytest.mark.unit
@pytest.mark.parametrize("env", [{"CWNG_PWA": "0"}, {"CWNG_SPA": "0"}])
def test_opt_out_serves_self_unregistering_worker(monkeypatch, tmp_path, env):
    app = _app(monkeypatch, tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    client = app.test_client()
    resp = client.get("/app/sw.js")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "__CWNG_SW__" not in body
    assert "self.registration.unregister()" in body
    assert "caches.delete" in body
    assert client.get("/app/manifest.webmanifest").status_code == 404


@pytest.mark.unit
def test_opt_out_drops_shell_tags(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setenv("CWNG_PWA", "off")
    html = app.test_client().get("/app/").get_data(as_text=True)
    assert "manifest.webmanifest" not in html
    assert "__CWNG_PWA__" not in html


@pytest.mark.unit
def test_worker_and_manifest_are_not_shadowed_by_shell_catch_all(monkeypatch, tmp_path):
    """/app/<path:path> serves the HTML shell; the two PWA files must win."""
    client = _app(monkeypatch, tmp_path).test_client()
    assert client.get("/app/sw.js").mimetype == "text/javascript"
    assert client.get("/app/manifest.webmanifest").mimetype == "application/manifest+json"
