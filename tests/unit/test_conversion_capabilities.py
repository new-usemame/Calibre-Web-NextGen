"""Behavioral checks for #1244's installed-Calibre format capability seam."""

from types import SimpleNamespace
import inspect
import os
import sys
import time

import pytest

from cps import helper
from cps.services import conversion_capabilities as capabilities


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_capability_cache():
    capabilities.clear_conversion_capability_cache()
    yield
    capabilities.clear_conversion_capability_cache()


def _book(*formats):
    return SimpleNamespace(data=[SimpleNamespace(format=value) for value in formats])


def test_installed_plugin_formats_are_offered_only_when_the_registry_reports_them(monkeypatch):
    monkeypatch.setattr(helper.config, "config_converterpath", "/calibre/ebook-convert", raising=False)
    monkeypatch.setattr(helper.config, "config_binariesdir", "/calibre", raising=False)
    monkeypatch.setattr(helper.config, "config_kepubifypath", "", raising=False)

    monkeypatch.setattr(
        helper,
        "get_conversion_capabilities",
        lambda *_: (frozenset({"epub", "kfx", "txt"}), frozenset({"epub", "kfx"})),
    )
    sources, _ = helper.get_convert_options(_book("KFX", "epub", "txt"))
    assert sources == ["kfx", "epub", "txt"]
    _, targets = helper.get_convert_options(_book("epub", "txt"))
    assert targets == ["kfx"]

    monkeypatch.setattr(
        helper,
        "get_conversion_capabilities",
        lambda *_: (frozenset({"epub", "txt"}), frozenset({"epub"})),
    )
    sources, targets = helper.get_convert_options(_book("kfx", "epub", "txt"))
    assert sources == ["epub", "txt"]
    assert targets == []


def test_probe_failure_hides_calibre_formats_but_preserves_independent_kepubify(monkeypatch):
    monkeypatch.setattr(helper.config, "config_converterpath", "/missing/ebook-convert", raising=False)
    monkeypatch.setattr(helper.config, "config_binariesdir", "/missing", raising=False)
    monkeypatch.setattr(helper.config, "config_kepubifypath", "/tools/kepubify", raising=False)
    monkeypatch.setattr(capabilities, "_calibre_debug_path", lambda *_: "")

    sources, targets = helper.get_convert_options(_book("epub", "kfx"))

    assert sources == ["epub"]
    assert targets == ["kepub"]


def test_calibre_debug_probe_uses_conversion_plugin_environment_and_refreshes_on_registry_change(
    tmp_path, monkeypatch
):
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    debug = binary_dir / "calibre-debug"
    debug.write_text("placeholder", encoding="utf-8")
    debug.chmod(0o755)
    config_dir = tmp_path / "calibre-config"
    config_dir.mkdir()
    registry = config_dir / "customize.py.json"
    registry.write_text('{"plugins": {"KFX Output": true}}', encoding="utf-8")
    monkeypatch.setenv("CWA_CALIBRE_USER_PLUGINS", "true")

    calls = []

    def run(debug_path, env):
        calls.append((debug_path, env))
        return 0, 'startup notice\nCWNG_CONVERSION_CAPABILITIES={"inputs": ["epub", "KFX"], "outputs": ["epub", "KFX"]}\n'

    monkeypatch.setattr(capabilities, "_run_bounded_probe", run)
    monkeypatch.setattr(capabilities.calibre_user_plugins, "apply_to_env", lambda env: {
        **env,
        "HOME": str(tmp_path),
        "CALIBRE_CONFIG_DIRECTORY": str(config_dir),
    })

    first = capabilities.get_conversion_capabilities(str(binary_dir / "ebook-convert"), str(binary_dir))
    second = capabilities.get_conversion_capabilities(str(binary_dir / "ebook-convert"), str(binary_dir))

    assert first == second == (frozenset({"epub", "kfx"}), frozenset({"epub", "kfx"}))
    assert len(calls) == 1
    probed_path, env = calls[0]
    assert probed_path == str(debug)
    assert env["HOME"] == str(tmp_path)
    assert env["CALIBRE_CONFIG_DIRECTORY"] == str(config_dir)

    # Replacing the registry contents invalidates the cache even if its path
    # stays fixed; this is the operator's common plugin-install/update path.
    registry.write_text('{"plugins": {"KFX Output": false}}', encoding="utf-8")
    third = capabilities.get_conversion_capabilities(str(binary_dir / "ebook-convert"), str(binary_dir))
    assert third == first
    assert len(calls) == 2


def test_invalid_or_unavailable_probe_fails_closed(tmp_path, monkeypatch):
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    debug = binary_dir / "calibre-debug"
    debug.write_text("placeholder", encoding="utf-8")
    debug.chmod(0o755)
    monkeypatch.delenv("CWA_CALIBRE_USER_PLUGINS", raising=False)

    monkeypatch.setattr(
        capabilities,
        "_run_bounded_probe",
        lambda *_args, **_kwargs: (0, 'CWNG_CONVERSION_CAPABILITIES={"inputs": ["epub"], "outputs": null}'),
    )
    assert capabilities.get_conversion_capabilities(str(binary_dir / "ebook-convert")) == (
        frozenset(),
        frozenset(),
    )
    debug.unlink()
    capabilities.clear_conversion_capability_cache()
    assert capabilities.get_conversion_capabilities(str(binary_dir / "ebook-convert")) == (
        frozenset(),
        frozenset(),
    )


def test_opt_in_and_default_plugin_environments_are_distinct_probe_keys(tmp_path, monkeypatch):
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    debug = binary_dir / "calibre-debug"
    debug.write_text("placeholder", encoding="utf-8")
    debug.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path / "ambient-home"))
    monkeypatch.delenv("CALIBRE_CONFIG_DIRECTORY", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    calls = []

    def run(_debug_path, env):
        calls.append(env)
        return 0, 'CWNG_CONVERSION_CAPABILITIES={"inputs": ["epub"], "outputs": ["epub"]}'

    monkeypatch.setattr(capabilities, "_run_bounded_probe", run)
    monkeypatch.delenv("CWA_CALIBRE_USER_PLUGINS", raising=False)
    capabilities.get_conversion_capabilities(str(binary_dir / "ebook-convert"))
    monkeypatch.setenv("CWA_CALIBRE_USER_PLUGINS", "true")
    capabilities.get_conversion_capabilities(str(binary_dir / "ebook-convert"))

    assert len(calls) == 2
    assert calls[0]["HOME"] == str(tmp_path / "ambient-home")
    assert "CALIBRE_CONFIG_DIRECTORY" not in calls[0]
    assert calls[1]["HOME"] == "/config"
    assert calls[1]["CALIBRE_CONFIG_DIRECTORY"] == "/config/.config/calibre"


def test_bare_converter_name_resolves_probe_beside_the_path_executable(tmp_path, monkeypatch):
    binary_dir = tmp_path / "calibre-bin"
    binary_dir.mkdir()
    converter = binary_dir / "ebook-convert"
    converter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    converter.chmod(0o755)
    debug = binary_dir / "calibre-debug"
    debug.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    debug.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary_dir))

    assert capabilities._calibre_debug_path("ebook-convert") == str(debug)


@pytest.mark.parametrize("body,expected_empty", [
    (f"import sys; sys.stdout.write('x' * {capabilities._MAX_PROBE_OUTPUT + 1000}); sys.stdout.flush()", True),
    ("import time; time.sleep(5)", True),
])
def test_probe_bounds_child_output_and_runtime(tmp_path, monkeypatch, body, expected_empty):
    debug = tmp_path / "calibre-debug"
    debug.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    debug.chmod(0o755)
    monkeypatch.setattr(capabilities, "_PROBE_TIMEOUT_SECONDS", 0.15)

    result = capabilities._run_bounded_probe(str(debug), os.environ.copy())

    assert (result is None) is expected_empty


def test_same_key_probes_coalesce_and_keep_the_gevent_hub_responsive(monkeypatch):
    gevent = pytest.importorskip("gevent")
    calls = []
    result = (frozenset({"epub"}), frozenset({"epub", "kfx"}))

    def slow_probe(converter_path, binaries_dir, env):
        calls.append((converter_path, binaries_dir))
        time.sleep(0.12)
        return result

    monkeypatch.setattr(capabilities, "_get_cached_capabilities", slow_probe)
    heartbeat = []

    def tick():
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            heartbeat.append(1)
            gevent.sleep(0.005)

    probes = [
        gevent.spawn(capabilities.get_conversion_capabilities, "/calibre/ebook-convert", "/calibre")
        for _ in range(2)
    ]
    ticker = gevent.spawn(tick)
    gevent.joinall(probes + [ticker], timeout=2)

    assert all(greenlet.dead for greenlet in probes + [ticker])
    assert [greenlet.value for greenlet in probes] == [result, result]
    assert calls == [("/calibre/ebook-convert", "/calibre")]
    assert len(heartbeat) >= 5


@pytest.mark.parametrize("allowed_targets,should_queue", [
    (["epub", "kfx"], True),
    (["epub"], False),
])
def test_classic_convert_post_revalidates_the_same_dynamic_targets(monkeypatch, allowed_targets, should_queue):
    from flask import Flask
    from cps import editbooks

    app = Flask(__name__)
    book = _book("epub")
    monkeypatch.setattr(editbooks.calibre_db, "get_filtered_book", lambda *_args, **_kwargs: book)
    monkeypatch.setattr(editbooks.helper, "get_convert_options", lambda _book: (["epub"], allowed_targets))
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: "/books")
    monkeypatch.setattr(editbooks, "current_user", SimpleNamespace(name="reader"))
    monkeypatch.setattr(editbooks, "flash", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(editbooks, "_", lambda message, **_kwargs: message)
    monkeypatch.setattr(editbooks, "url_for", lambda *_args, **_kwargs: "/book/42/edit")
    queued = []
    monkeypatch.setattr(
        editbooks.helper,
        "convert_book_format",
        lambda *args: queued.append(args) or None,
    )

    with app.test_request_context(
        "/admin/book/convert/42",
        method="POST",
        data={"book_format_from": "EPUB", "book_format_to": "KFX"},
    ):
        response = inspect.unwrap(editbooks.convert_bookformat)(42)

    assert bool(queued) is should_queue
    if should_queue:
        assert queued[0][2:4] == ("EPUB", "KFX")
    else:
        assert response.status_code == 302


def test_native_calibre_internal_input_name_does_not_hide_valid_output_formats():
    # The packaged Calibre registry includes downloaded_recipe, even for an
    # ordinary EPUB book. One legitimate internal name must not fail the entire
    # inventory and remove every valid conversion from both editors.
    result = capabilities._parse_probe(
        'CWNG_CONVERSION_CAPABILITIES={"inputs":["epub","downloaded_recipe"],"outputs":["txt","epub"]}\n'
    )
    assert result == (frozenset({"epub", "downloaded_recipe"}), frozenset({"txt", "epub"}))


def test_directory_only_oeb_output_is_not_offered_as_a_stored_book_file(monkeypatch):
    monkeypatch.setattr(helper.config, "config_converterpath", "/calibre/ebook-convert", raising=False)
    monkeypatch.setattr(helper.config, "config_binariesdir", "/calibre", raising=False)
    monkeypatch.setattr(helper.config, "config_kepubifypath", "", raising=False)
    monkeypatch.setattr(helper, "get_conversion_capabilities", lambda *_:
        (frozenset({"epub"}), frozenset({"epub", "txt", "oeb"})))
    assert helper.get_convert_options(_book("epub")) == (["epub"], ["txt"])
