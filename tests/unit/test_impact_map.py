"""Behavioral tests for the static impact-map generator and query interface."""

from __future__ import annotations

import importlib.util
import json
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
        "    return callback()\n",
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


def test_committed_recall_report_is_reproducible_and_keeps_misses():
    """Intent: acceptance evidence comes from real available commits and never hides misses."""
    data = impact_map.load_json(ROOT / "state/modernization/impact-map.json")
    cases = impact_map.load_json(ROOT / "state/modernization/impact-map-recall-cases.json")
    committed = impact_map.load_json(ROOT / "state/modernization/impact-map-recall.json")

    observed = impact_map.evaluate_recall(data, cases, ROOT)

    assert observed == committed
    assert observed["total"] >= 8
    assert observed["hits"] == 8
    assert observed["misses"] == 2
    assert observed["hit_rate"] == 0.8
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
