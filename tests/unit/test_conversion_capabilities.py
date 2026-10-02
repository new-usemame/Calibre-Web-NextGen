"""Behavioral checks for #1244's installed-Calibre format capability seam."""

from types import SimpleNamespace
import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

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


def test_slow_failure_cache_backoff_starts_when_probe_finishes(tmp_path, monkeypatch):
    debug = tmp_path / "calibre-debug"
    debug.write_text("placeholder", encoding="utf-8")
    debug.chmod(0o755)
    clock = {"now": 100.0}
    monkeypatch.setattr(capabilities, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    calls = []

    def timed_out_probe(*_args):
        calls.append(clock["now"])
        clock["now"] += capabilities._PROBE_TIMEOUT_SECONDS
        return None

    monkeypatch.setattr(capabilities, "_run_bounded_probe", timed_out_probe)
    env = {"HOME": str(tmp_path)}
    converter = str(tmp_path / "ebook-convert")
    empty = (frozenset(), frozenset())
    assert capabilities._get_cached_capabilities(converter, "", env) == empty
    completed = clock["now"]

    for elapsed in (0, capabilities._FAILURE_TTL_SECONDS - .1):
        clock["now"] = completed + elapsed
        assert capabilities._get_cached_capabilities(converter, "", env) == empty
        assert len(calls) == 1, "A slow probe must retain the full failure backoff after completion"

    clock["now"] = completed + capabilities._FAILURE_TTL_SECONDS + .1
    assert capabilities._get_cached_capabilities(converter, "", env) == empty
    assert len(calls) == 2, "The failed capability probe must retry when its backoff expires"


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


def _probe_program(tmp_path, monkeypatch, body, processes=None):
    """Use real child I/O without assuming an interpreter-safe shebang path."""
    debug = tmp_path / "debug program with spaces.py"
    started = tmp_path / "started"
    debug.write_text(
        "import os, sys, time, subprocess\n"
        f"open({str(started)!r}, 'w').write('started')\n" + body + "\n",
        encoding="utf-8",
    )
    real_popen = subprocess.Popen

    def launch(command, **kwargs):
        assert command == [str(debug), "-c", capabilities._PROBE_CODE]
        process = real_popen([sys.executable, str(debug)], **kwargs)
        if processes is not None:
            processes.append(process)
        return process

    monkeypatch.setattr(capabilities.subprocess, "Popen", launch)
    return debug, started


def test_probe_success_control_runs_child_and_parses_its_registry(tmp_path, monkeypatch):
    payload = {"inputs": ["epub"], "outputs": ["txt"]}
    debug, started = _probe_program(
        tmp_path, monkeypatch,
        f"print({capabilities._PROBE_MARKER + json.dumps(payload)!r}, flush=True)",
    )
    result = capabilities._run_bounded_probe(str(debug), os.environ.copy())
    assert started.read_text() == "started"
    assert result is not None and result[0] == 0
    assert capabilities._parse_probe(result[1]) == (frozenset({"epub"}), frozenset({"txt"}))


def test_probe_without_nonblocking_pipe_support_fails_closed_and_reaps_child(tmp_path, monkeypatch):
    processes = []
    debug, _started = _probe_program(tmp_path, monkeypatch, "time.sleep(30)", processes)

    def unsupported(_descriptor, _blocking):
        raise NotImplementedError("This runtime cannot poll pipe output")

    monkeypatch.setattr(capabilities.os, "set_blocking", unsupported)
    assert capabilities._run_bounded_probe(str(debug), os.environ.copy()) is None
    assert len(processes) == 1
    assert processes[0].returncode is not None, "The launched child must be reaped on setup failure"
    assert processes[0].stdout.closed, "The failed probe must close its output descriptor"


@pytest.mark.parametrize("body", [
    f"sys.stdout.write('x' * {capabilities._MAX_PROBE_OUTPUT + 1000}); sys.stdout.flush()",
    "time.sleep(5)",
], ids=["output-overflow", "timeout"])
def test_probe_bounds_child_output_and_runtime(tmp_path, monkeypatch, body):
    debug, started = _probe_program(tmp_path, monkeypatch, body)
    monkeypatch.setattr(capabilities, "_PROBE_TIMEOUT_SECONDS", 1.0)

    before = time.monotonic()
    result = capabilities._run_bounded_probe(str(debug), os.environ.copy())
    elapsed = time.monotonic() - before

    assert started.read_text() == "started", "The intended body must run before the bound is tested"
    assert result is None
    assert elapsed < 3, f"A five-second sleeper must be terminated, not awaited ({elapsed:.3f}s)"


def _process_running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed orphan can remain a zombie until the host's reaper collects it.
    # It holds no executable resources or inherited output descriptors.
    if sys.platform.startswith("linux"):
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    return True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups; Windows tree cleanup is a separate platform path")
@pytest.mark.parametrize("parent_body", [
    "pass", "time.sleep(30)",
    f"sys.stdout.write('x' * {capabilities._MAX_PROBE_OUTPUT + 1000}); sys.stdout.flush(); time.sleep(30)",
], ids=["leader-exits", "timeout", "overflow"])
def test_failed_probe_reaps_descendant_and_output_reader(tmp_path, monkeypatch, parent_body):
    """A helper inheriting the pipe must not survive a failed capability probe."""
    child_pid = tmp_path / "descendant.pid"
    child_started = tmp_path / "descendant.started"
    child_code = f"open({str(child_started)!r}, 'w').write('started'); import time; time.sleep(30)"
    debug, started = _probe_program(
        tmp_path, monkeypatch,
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"open({str(child_pid)!r}, 'w').write(str(child.pid))\n"
        f"while not os.path.exists({str(child_started)!r}): time.sleep(.01)\n"
        + parent_body,
    )
    monkeypatch.setattr(capabilities, "_PROBE_TIMEOUT_SECONDS", 1.0)
    readers_before = {t.ident for t in threading.enumerate() if t.name == "calibre-capability-output"}
    pid = None
    try:
        result = capabilities._run_bounded_probe(str(debug), os.environ.copy())
        assert started.read_text() == child_started.read_text() == "started"
        pid = int(child_pid.read_text())
        assert result is None
        deadline = time.monotonic() + 1
        while _process_running(pid) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not _process_running(pid), "A failed probe left its inherited-output helper running"
        readers_after = {t.ident for t in threading.enumerate() if t.name == "calibre-capability-output"}
        assert readers_after <= readers_before, "A failed probe left an output reader blocked on its helper"
    finally:
        # Preserve a seen-red run without leaving its owned process behind.
        if pid is None and child_pid.exists():
            pid = int(child_pid.read_text())
        if pid is not None and _process_running(pid):
            os.kill(pid, signal.SIGKILL)


def test_same_key_probes_coalesce_and_keep_the_gevent_hub_responsive(monkeypatch):
    gevent = pytest.importorskip("gevent")
    # Native subprocess tests may leave gevent's cached loop clock stale.
    # Refresh it cooperatively before installing the responsiveness deadline.
    gevent.sleep(0)
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
