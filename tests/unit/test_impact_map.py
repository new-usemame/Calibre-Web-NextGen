"""Behavioral tests for the static impact-map generator and query interface."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "impact_map.py"
SPEC = importlib.util.spec_from_file_location("impact_map_tool", SCRIPT)
impact_map = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = impact_map
SPEC.loader.exec_module(impact_map)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def miniature_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    write(repo / "cps" / "__init__.py", "")
    write(
        repo / "cps" / "provider.py",
        "def imported():\n    return 1\n\n"
        "def guessed():\n    return 2\n\n"
        "class Worker:\n    def run(self):\n        return 3\n",
    )
    write(
        repo / "cps" / "app.py",
        "from flask import Blueprint\n"
        "from .provider import imported\n"
        "import cps.provider as provider\n\n"
        "bp = Blueprint('sample', __name__)\n\n"
        "@bp.route('/entry')\n"
        "def entry():\n"
        "    imported()\n"
        "    provider.guessed()\n"
        "    return 'ok'\n\n"
        "def dynamic(receiver, callback):\n"
        "    receiver.guessed()\n"
        "    return callback()\n\n"
        "def accounting():\n"
        "    from .provider import Worker\n"
        "    len([])\n"
        "    Worker.run()\n",
    )
    oracle = repo / "oracle.json"
    oracle.write_text(
        json.dumps({
            "schema_version": 1,
            "oracle_id": "test-oracle",
            "source_repo_sha": "0" * 40,
            "static_routes": {
                "count": 1,
                "errors": [],
                "records": [{
                    "blueprint_var": "bp",
                    "file": "cps/app.py",
                    "func": "entry",
                    "line": 7,
                    "methods": ["GET"],
                    "rule": "/entry",
                }],
            },
            "reconciliation": {
                "runtime_union_distinct_keys": 2,
                "static_total_records": 1,
                "static_distinct_keys": 1,
                "static_only": [],
                "unmapped_static_records": [],
                "var_to_blueprint": {"bp": {"name": "sample", "url_prefix": None}},
                "runtime_only": [{
                    "endpoint": "static",
                    "rule": "/static/<path:filename>",
                    "seen_in_shapes": ["test"],
                }],
            },
        }, indent=2),
        encoding="utf-8",
    )
    return repo, oracle


@pytest.fixture
def miniature_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, oracle = miniature_repo(tmp_path)
    monkeypatch.setattr(impact_map, "git_sha", lambda _root: "1" * 40)
    monkeypatch.setattr(impact_map, "git_object_sha", lambda _root, _revision: "a" * 40)
    return impact_map.build_map(repo, oracle)


def test_generator_separates_exact_bindings_from_attribute_guesses(miniature_map):
    """Intent: exact imports stay distinguishable while unknown receivers remain visible as blind spots."""
    calls = [edge for edge in miniature_map["edges"] if edge["kind"] == "call"]
    confidences = {edge["confidence"] for edge in calls}

    assert "exact_import_symbol" in confidences
    assert "exact_import_module_attribute" in confidences
    assert "attribute_name_guess" in confidences

    guessed = [spot for spot in miniature_map["blind_spots"] if spot["reason"] == "receiver_unknown_attribute_name_guess"]
    callbacks = [spot for spot in miniature_map["blind_spots"] if spot["reason"] == "local_value_or_parameter_call"]
    assert [(spot["file"], spot["line"], spot["shape"]) for spot in guessed] == [
        ("cps/app.py", 14, "attribute_on_name")
    ]
    assert [(spot["file"], spot["line"], spot["callee"]) for spot in callbacks] == [
        ("cps/app.py", 15, "callback")
    ]
    assert miniature_map["blind_spot_summary"]["by_module"]["cps.app"]["unresolved_calls"] > 0


def test_route_query_reaches_handler_and_reports_module_blindness(miniature_map):
    """Intent: a route query exposes downstream calls and the module's unresolved sites together."""
    result = impact_map.query_map(miniature_map, "/entry", depth=3)

    assert [node["endpoint"] for node in result["matched_nodes"]] == ["sample.entry"]
    reached_ids = {item["node"] for item in result["reaches"]}
    assert "symbol:cps.app:entry" in reached_ids
    assert "symbol:cps.provider:imported" in reached_ids
    assert "symbol:cps.provider:guessed" in reached_ids
    assert [node["endpoint"] for node in result["live_routes_reaching_target"]] == ["sample.entry"]
    assert result["blind_spots"]["count"] > 0


def test_runtime_only_route_is_live_but_has_no_invented_handler(miniature_map):
    """Intent: dynamic runtime routes are explained without fabricating a cps source edge."""
    node = next(node for node in miniature_map["nodes"] if node.get("endpoint") == "static")
    outgoing = [edge for edge in miniature_map["edges"] if edge["source"] == node["id"]]

    assert node["runtime_status"] == "runtime_only"
    assert node["runtime_only_kind"] == "flask_builtin_static"
    assert node["live"] is True
    assert outgoing == []


def test_reconciliation_static_only_route_is_not_claimed_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Intent: a future static-only reconciliation result must remain visibly non-live."""
    repo, oracle_path = miniature_repo(tmp_path)
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    oracle["reconciliation"]["static_only"] = [
        {"endpoint": "sample.entry", "rule": "/entry"}
    ]
    oracle_path.write_text(json.dumps(oracle), encoding="utf-8")
    monkeypatch.setattr(impact_map, "git_sha", lambda _root: "3" * 40)
    monkeypatch.setattr(impact_map, "git_object_sha", lambda _root, _revision: "c" * 40)

    data = impact_map.build_map(repo, oracle_path)
    route = next(node for node in data["nodes"] if node.get("endpoint") == "sample.entry")

    assert route["runtime_status"] == "reconciled_static_only"
    assert route["live"] is False
    assert data["counts"]["reconciled_current_routes"] == 0
    assert data["counts"]["reconciled_static_only_routes"] == 1


def test_same_inputs_generate_byte_identical_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Intent: regeneration at one repository SHA is byte-for-byte deterministic."""
    repo, oracle = miniature_repo(tmp_path)
    monkeypatch.setattr(impact_map, "git_sha", lambda _root: "2" * 40)
    monkeypatch.setattr(impact_map, "git_object_sha", lambda _root, _revision: "b" * 40)

    first = impact_map.stable_json(impact_map.build_map(repo, oracle))
    second = impact_map.stable_json(impact_map.build_map(repo, oracle))

    assert first == second


def test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind(miniature_map):
    """Intent: dropping builtins or coarse-call blind records must fail before artifact regeneration."""
    counts = miniature_map["counts"]
    summary = miniature_map["blind_spot_summary"]
    assert counts["call_sites"] == (
        counts["exact_internal_call_edges"]
        + counts["known_out_of_scope_calls"]
        + summary["unresolved_calls"]
    )
    assert summary["known_out_of_scope_calls"]["by_reason"]["builtin_call"] == 1
    coarse = [edge for edge in miniature_map["edges"] if edge["confidence"] == "class_member_coarse"]
    assert len(coarse) == 1
    for edge in miniature_map["edges"]:
        if edge["confidence"] in {"attribute_name_guess", "class_member_coarse"}:
            assert any(
                spot["kind"] == "unresolved_call"
                and all(spot[key] == value for key, value in edge["location"].items())
                and edge["target"] in spot["candidate_targets"]
                for spot in miniature_map["blind_spots"]
            ), edge
    assert sum(module["call_sites"] for module in summary["by_module"].values()) == counts["call_sites"]
    assert sum(module["unresolved_calls"] for module in summary["by_module"].values()) == summary["unresolved_calls"]


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=fixture", "-c",
         "user.email=fixture@example.invalid", *args],
        cwd=repo, text=True, stderr=subprocess.PIPE,
    ).strip()


@pytest.fixture
def history_repo(tmp_path: Path):
    repo, oracle = miniature_repo(tmp_path)
    write(repo / impact_map.DEFAULT_ORACLE, oracle.read_text(encoding="utf-8"))
    git(repo, "init")
    git(repo, "commit", "--allow-empty", "-m", "Start synthetic history")
    git(repo, "add", "cps")
    git(repo, "commit", "-m", "Seed synthetic callers and providers")
    commit = git(repo, "rev-parse", "HEAD")
    cases = {"case_set": "synthetic", "cases": [
        {"commit": commit, "affected_site": "cps.app:entry",
         "changed_symbol": "cps.provider:imported"},
        {"commit": commit, "affected_site": "cps.app:dynamic",
         "changed_symbol": "cps.provider:Worker"},
    ]}
    impact_map.write_json(repo / impact_map.DEFAULT_CASES, cases)
    data = impact_map.build_map(repo, repo / impact_map.DEFAULT_ORACLE)
    impact_map.write_json(repo / impact_map.DEFAULT_MAP, data, compact=True)
    impact_map.write_json(repo / impact_map.DEFAULT_RECALL, impact_map.evaluate_recall(data, cases, repo))
    return repo


def refresh(repo: Path, output: Path, summary: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo), "refresh",
         "--output-dir", str(output), "--summary", str(summary)],
        capture_output=True, text=True, timeout=30,
    )


def test_refresh_publishes_currency_without_requiring_contributor_updates(history_repo, tmp_path):
    """Intent: real source drift regenerates evidence, returns success, and leaves contributor files alone."""
    repo = history_repo
    output, summary = tmp_path / "artifacts", tmp_path / "summary.md"
    original = {path: (repo / path).read_bytes() for path in (impact_map.DEFAULT_MAP, impact_map.DEFAULT_RECALL)}
    result = refresh(repo, output, summary)
    assert result.returncode == 0, result.stderr
    currency = impact_map.load_json(output / "impact-map-currency.json")
    assert currency["status"] == "current"

    write(repo / "cps/new.py", "def added():\n    return len([])\n")
    git(repo, "add", "cps/new.py")
    git(repo, "commit", "-m", "Add a synthetic module without regenerating artifacts")
    result = refresh(repo, output, summary)
    assert result.returncode == 0, result.stderr
    currency = impact_map.load_json(output / "impact-map-currency.json")
    assert currency["status"] == "stale"
    assert currency["current_cps_tree_sha"] == git(repo, "rev-parse", "HEAD:cps")
    assert currency["committed_cps_tree_sha"] != currency["current_cps_tree_sha"]
    assert currency["drift"] == {"impact-map.json": True, "impact-map-recall.json": True}
    for path, content in original.items():
        assert (repo / path).read_bytes() == content
    fresh = impact_map.load_json(output / "impact-map.json")
    assert any(node["id"] == "symbol:cps.new:added" for node in fresh["nodes"])
    report = impact_map.load_json(output / "impact-map-recall.json")
    assert (report["hits"], report["misses"]) == (1, 1)
    assert report["results"][1]["miss_reason"] == "no_static_call_path"
    text = summary.read_text(encoding="utf-8")
    assert "stale" in text and currency["current_cps_tree_sha"] in text
    assert "1/2" in text and "no_static_call_path" in text

    for name in currency["drift"]:
        shutil.copyfile(output / name, repo / impact_map.DEFAULT_MAP.parent / name)
    assert refresh(repo, output, summary).returncode == 0
    assert impact_map.load_json(output / "impact-map-currency.json")["status"] == "current"

    # A matching cps fingerprint cannot hide generator/oracle/report drift.
    for path in (impact_map.DEFAULT_MAP, impact_map.DEFAULT_RECALL):
        original_bytes = (repo / path).read_bytes()
        changed = json.loads(original_bytes)
        if path == impact_map.DEFAULT_MAP:
            changed["counts"]["call_sites"] += 1
        else:
            changed["hits"] += 1
        impact_map.write_json(repo / path, changed)
        assert refresh(repo, output, summary).returncode == 0
        assert impact_map.load_json(output / "impact-map-currency.json")["drift"][path.name]
        (repo / path).unlink()
        assert refresh(repo, output, summary).returncode == 0
        assert impact_map.load_json(output / "impact-map-currency.json")["drift"][path.name]
        (repo / path).write_bytes(original_bytes)


def test_refresh_rejects_unavailable_recall_history(history_repo, tmp_path):
    """Intent: missing history is an evaluation failure, not a measured recall miss or mere staleness."""
    path = history_repo / impact_map.DEFAULT_CASES
    cases = impact_map.load_json(path)
    cases["cases"][0]["commit"] = "0" * 40
    impact_map.write_json(path, cases)
    result = refresh(history_repo, tmp_path / "artifacts", tmp_path / "summary.md")
    assert result.returncode != 0
    assert "recall evidence unavailable" in result.stderr


def test_refresh_refuses_to_overwrite_committed_inputs(history_repo, tmp_path):
    """Intent: a mistaken output directory must not erase the evidence used to measure staleness."""
    before = (history_repo / impact_map.DEFAULT_MAP).read_bytes()
    result = refresh(history_repo, history_repo / impact_map.DEFAULT_MAP.parent, tmp_path / "summary.md")
    assert result.returncode != 0
    assert "output directory would overwrite committed artifacts" in result.stderr
    assert (history_repo / impact_map.DEFAULT_MAP).read_bytes() == before


@pytest.mark.parametrize("destination,alias", [
    *[(name, "direct") for name in (
        "committed-map", "committed-recall", "oracle", "cases", "source",
        "generated-map", "generated-recall", "currency",
    )],
    ("committed-map", "symlink"), ("currency", "symlink"),
    ("cases", "hardlink"), ("generated-map", "hardlink"),
    ("currency", "absent"),
])
def test_refresh_rejects_summary_aliases_before_any_write(history_repo, tmp_path, destination, alias):
    """Intent: an aliased summary must fail before replacing evidence or appending to an input."""
    repo, output = history_repo, tmp_path / "artifacts"
    assert refresh(repo, output, tmp_path / "summary.md").returncode == 0
    protected = {
        "committed-map": repo / impact_map.DEFAULT_MAP,
        "committed-recall": repo / impact_map.DEFAULT_RECALL,
        "oracle": repo / impact_map.DEFAULT_ORACLE,
        "cases": repo / impact_map.DEFAULT_CASES,
        "source": repo / "cps/app.py",
        "generated-map": output / impact_map.DEFAULT_MAP.name,
        "generated-recall": output / impact_map.DEFAULT_RECALL.name,
        "currency": output / "impact-map-currency.json",
    }
    summary = protected[destination]
    if alias == "symlink":
        summary = tmp_path / "summary-link"
        summary.symlink_to(protected[destination])
    elif alias == "hardlink":
        summary = tmp_path / "summary-link"
        summary.hardlink_to(protected[destination])
    elif alias == "absent":
        shutil.rmtree(output)
    # A late rejection would replace the old graph with this changed source.
    write(repo / "cps/new.py", "def added():\n    return 1\n")
    before = {path: path.read_bytes() if path.exists() else None for path in protected.values()}

    result = refresh(repo, output, summary)

    after = {path: path.read_bytes() if path.exists() else None for path in protected.values()}
    assert after == before, "summary collision changed protected bytes before rejection"
    assert result.returncode != 0, result.stdout
    assert "summary destination aliases" in result.stderr
    if alias == "absent":
        assert not output.exists(), "collision rejection must precede output creation"


@pytest.mark.parametrize("failure", ["missing-history", "invalid-cases", "invalid-oracle"])
def test_failed_refresh_invalidates_previous_currency(history_repo, tmp_path, failure):
    """Intent: a failed second revision must never leave a successful first-revision currency label."""
    repo, output, summary = history_repo, tmp_path / "artifacts", tmp_path / "summary.md"
    assert refresh(repo, output, summary).returncode == 0
    currency = output / "impact-map-currency.json"
    previous = impact_map.load_json(currency)
    assert previous["status"] == "current"
    write(repo / "cps/new.py", "def added():\n    return 1\n")
    git(repo, "add", "cps/new.py")
    git(repo, "commit", "-m", "Advance synthetic source revision")
    assert git(repo, "rev-parse", "HEAD:cps") != previous["current_cps_tree_sha"]
    case_path, oracle_path = repo / impact_map.DEFAULT_CASES, repo / impact_map.DEFAULT_ORACLE
    cases, oracle = case_path.read_bytes(), oracle_path.read_bytes()
    if failure == "missing-history":
        invalid = json.loads(cases)
        invalid["cases"][0]["commit"] = "0" * 40
        impact_map.write_json(case_path, invalid)
    elif failure == "invalid-cases":
        write(case_path, "invalid JSON\n")
    else:
        write(oracle_path, "invalid JSON\n")
    previous_summary = summary.read_bytes()

    result = refresh(repo, output, summary)

    assert result.returncode != 0
    assert not currency.exists(), "failed refresh retained a successful currency label from the previous revision"
    assert summary.read_bytes() == previous_summary
    # A later valid attempt can publish a coherent set in the same directory.
    case_path.write_bytes(cases)
    oracle_path.write_bytes(oracle)
    assert refresh(repo, output, summary).returncode == 0
    recovered = impact_map.load_json(currency)
    fresh = impact_map.load_json(output / impact_map.DEFAULT_MAP.name)
    assert recovered["current_cps_tree_sha"] == git(repo, "rev-parse", "HEAD:cps")
    assert fresh["generated_from"]["cps_tree_sha"] == recovered["current_cps_tree_sha"]
    assert recovered["status"] == "stale"


def test_committed_recall_report_is_reproducible_and_keeps_misses():
    """Intent: acceptance evidence comes from real available commits and never hides misses."""
    data = impact_map.load_json(ROOT / "state/modernization/impact-map.json")
    cases = impact_map.load_json(ROOT / "state/modernization/impact-map-recall-cases.json")
    committed = impact_map.load_json(ROOT / "state/modernization/impact-map-recall.json")

    observed = impact_map.evaluate_recall(data, cases, ROOT)

    assert observed == committed
    assert observed["total"] >= 8
    assert observed["total"] == len(cases["cases"]) == len(observed["results"])
    assert [result["commit"] for result in observed["results"]] == [case["commit"] for case in cases["cases"]]
    assert len({case["commit"] for case in cases["cases"]}) == observed["total"]
    hits = sum(result["hit"] for result in observed["results"])
    assert observed["hits"] == hits
    assert observed["misses"] == observed["total"] - hits
    assert observed["hit_rate"] == round(hits / observed["total"], 6)
    assert observed["hit_rate_percent"] == round(100 * hits / observed["total"], 2)
    assert all(result["miss_reason"] for result in observed["results"] if not result["hit"])
    assert all(result["commit_exists"] for result in observed["results"])
    assert all(result["evidence_paths_present"] for result in observed["results"])


def test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor():
    """Intent: the shipped artifact cannot present itself as complete or lose its pinned runtime join."""
    data = impact_map.load_json(ROOT / "state/modernization/impact-map.json")

    assert data["counts"]["blind_spots"] > 0
    assert data["counts"]["call_sites"] > data["counts"]["exact_internal_call_edges"]
    assert data["counts"]["call_sites"] == (
        data["counts"]["exact_internal_call_edges"]
        + data["counts"]["known_out_of_scope_calls"]
        + data["blind_spot_summary"]["unresolved_calls"]
    )
    assert data["counts"]["current_static_routes"] == 521
    assert data["counts"]["reconciled_current_routes"] == 521
    assert data["counts"]["runtime_only_routes"] == 7
    assert data["counts"]["reconciled_static_only_routes"] == 0
    assert data["counts"]["current_only_route_drift"] == 0
    assert data["counts"]["oracle_only_route_drift"] == 0
    assert len(data["blind_spot_summary"]["by_module"]) == data["counts"]["modules"]
