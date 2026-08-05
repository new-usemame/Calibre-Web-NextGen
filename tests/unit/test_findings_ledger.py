"""Behavioural tests for the findings ledger CLI (scripts/findings.py).

The ledger holds maintenance-pass observations that used to be filed as GitHub issues and
drowned the tracker. Its value depends on three properties, so those are what is pinned
here: re-filing the same observation must not create a duplicate, a write must never leave
a half-written file behind, and nothing must be able to enter with a severity or status the
index cannot rank.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "scripts" / "findings.py"


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """A findings.py bound to an empty temp ledger, so tests never touch the real one."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("findings_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ITEMS", tmp_path / "items")
    monkeypatch.setattr(mod, "INDEX", tmp_path / "INDEX.md")
    return mod


def _add(mod, title, *, area="kobo", severity="correctness", body="detail", force=False):
    return mod.main(
        ["add", title, "--area", area, "--severity", severity, "--body", body]
        + (["--force"] if force else [])
    )


# --------------------------------------------------------------- identity


def test_same_observation_twice_does_not_duplicate(ledger, capsys):
    """The whole point: a later pass re-noticing something must not refile it.

    Without this the ledger reacquires exactly the duplication problem that moving off the
    issue tracker was meant to solve.
    """
    _add(ledger, "Kobo sync overwrites a further position with an older one")
    capsys.readouterr()
    _add(ledger, "Kobo sync overwrites a further position with an older one")
    out = capsys.readouterr().out

    assert "already exists" in out
    assert len(list((ledger.ITEMS).glob("F-*.json"))) == 1


def test_id_ignores_word_order_and_case_but_not_area(ledger):
    """Ids key on content words, so trivial rewording collapses; a different subsystem does not."""
    a = ledger._make_id("Kobo sync loses reading position", "kobo")
    b = ledger._make_id("reading position LOSES kobo sync", "kobo")
    c = ledger._make_id("Kobo sync loses reading position", "koreader")

    assert a == b, "reordered/recased title should be the same finding"
    assert a != c, "same words in a different subsystem is a different finding"


def test_distinct_observations_get_distinct_ids(ledger):
    _add(ledger, "Ingest drops a format when automerge overwrites", area="ingest")
    _add(ledger, "Reader retry timer outlives the reader", area="spa")
    assert len(list((ledger.ITEMS).glob("F-*.json"))) == 2


# --------------------------------------------------------------- validation


@pytest.mark.parametrize("bad", ["urgent", "", "SECURITY", "p0"])
def test_unrankable_severity_is_rejected(ledger, bad):
    """A severity the index cannot sort would silently sink to the bottom of the ranking."""
    with pytest.raises(SystemExit):  # argparse rejects it before it reaches disk
        ledger.main(["add", "x", "--area", "kobo", "--severity", bad])
    assert not list((ledger.ITEMS).glob("F-*.json"))


def test_empty_title_is_rejected(ledger):
    assert _add(ledger, "   ") == 2
    assert not list((ledger.ITEMS).glob("F-*.json"))


def test_severity_ordering_is_total_and_urgent_first(ledger):
    """Index ordering is the product surface; an unranked severity would break it."""
    assert ledger.SEVERITIES[0] == "security"
    assert ledger.SEVERITIES[1] == "data-integrity"
    assert len(set(ledger.SEVERITIES)) == len(ledger.SEVERITIES)
    assert all(s in ledger.SEV_RANK for s in ledger.SEVERITIES)


# --------------------------------------------------------------- durability


def test_body_is_preserved_verbatim(ledger):
    """Findings carry migrated issue text; silent mangling would be real data loss."""
    body = "Line one\n\n  indented\n\n```py\nx = 1  # a | pipe and *stars*\n```\n"
    ledger.main(["add", "Some finding", "--area", "api", "--severity", "test", "--body", body])
    rec = json.loads(next((ledger.ITEMS).glob("F-*.json")).read_text())
    assert rec["body"] == body.strip()


def test_write_leaves_no_temp_file_behind(ledger):
    _add(ledger, "A finding")
    leftovers = [p.name for p in (ledger.ITEMS).iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_corrupt_file_is_skipped_not_fatal(ledger, capsys):
    """One hand-mangled file must not take down `list` for every other finding."""
    _add(ledger, "A good finding")
    (ledger.ITEMS / "F-bad000.json").write_text("{not json")

    assert ledger.main(["list", "--quiet"]) == 0
    assert "F-bad000" in capsys.readouterr().err


# --------------------------------------------------------------- lifecycle


def test_resolve_records_release_and_leaves_open_list(ledger, capsys):
    _add(ledger, "Fixed thing")
    fid = json.loads(next((ledger.ITEMS).glob("F-*.json")).read_text())["id"]
    capsys.readouterr()

    ledger.main(["resolve", fid, "--release", "v4.1.31", "--commit", "abc1234"])
    capsys.readouterr()  # drop resolve's own echo so only `list` output is asserted on
    ledger.main(["list", "--quiet"])
    assert fid not in capsys.readouterr().out, "resolved finding should leave the open list"

    rec = json.loads((ledger.ITEMS / f"{fid}.json").read_text())
    assert rec["status"] == "resolved"
    assert rec["resolution"]["release"] == "v4.1.31"


def test_promote_links_the_issue_and_closes_the_finding(ledger):
    """Promotion happens when a user reports it independently; the thread becomes theirs."""
    _add(ledger, "Thing a user also hit")
    fid = json.loads(next((ledger.ITEMS).glob("F-*.json")).read_text())["id"]

    ledger.main(["promote", fid, "1391"])
    rec = json.loads((ledger.ITEMS / f"{fid}.json").read_text())
    assert rec["status"] == "resolved"
    assert "#1391" in rec["refs"]
    assert "1391" in rec["resolution"]["note"]


def test_resolve_unknown_id_fails_loudly(ledger):
    assert ledger.main(["resolve", "F-nope00"]) == 1


# --------------------------------------------------------------- index


def test_index_states_it_is_not_the_bug_tracker(ledger):
    """A reader landing here from search must be sent to the tracker, not file nothing."""
    _add(ledger, "Some finding")
    ledger.main(["index"])
    text = (ledger.INDEX).read_text()

    assert "not the" in text.lower() and "bug tracker" in text.lower()
    assert "github.com/new-usemame/Calibre-Web-NextGen/issues" in text


def test_index_orders_severity_urgent_first(ledger):
    _add(ledger, "A cosmetic thing", severity="chore")
    _add(ledger, "A data losing thing", area="ingest", severity="data-integrity")
    ledger.main(["index"])
    text = (ledger.INDEX).read_text()

    assert text.index("### data-integrity") < text.index("### chore")


def test_index_escapes_pipes_so_tables_survive(ledger):
    """A title with a pipe would otherwise split the markdown row into bogus columns."""
    _add(ledger, "Route a|b breaks the thing")
    ledger.main(["index"])
    row = [l for l in (ledger.INDEX).read_text().splitlines() if "breaks the thing" in l][0]
    assert r"a\|b" in row, "pipe in a title must be escaped"
    # Count real column separators: drop the escaped pipe before splitting.
    assert row.replace(r"\|", "~").count("|") == 4, "escaped pipe must not add a column"


def test_index_is_deterministic(ledger):
    """Regenerating without changes must not produce a diff, or every run dirties the tree."""
    _add(ledger, "One")
    _add(ledger, "Two", area="spa")
    ledger.main(["index"])
    first = (ledger.INDEX).read_text()
    ledger.main(["index"])
    assert (ledger.INDEX).read_text() == first


# --------------------------------------------------------------- dedupe


def test_dedupe_flags_reworded_near_duplicate(ledger, capsys):
    _add(ledger, "Kobo sync overwrites a further reading position with an older one")
    _add(ledger, "Kobo sync overwrites reading position with older device position", force=True)
    capsys.readouterr()

    ledger.main(["dedupe"])
    assert "candidate pair" in capsys.readouterr().out


def test_dedupe_quiet_on_unrelated_findings(ledger, capsys):
    _add(ledger, "Ingest drops a format on automerge", area="ingest")
    _add(ledger, "Translations fall back to English", area="i18n")
    capsys.readouterr()

    ledger.main(["dedupe"])
    assert "no near-duplicates" in capsys.readouterr().out


# --------------------------------------------------------------- cli contract


def test_runs_as_a_subprocess_with_no_third_party_imports():
    """Project rule 6: no new dependencies. The tool must run on a bare interpreter."""
    r = subprocess.run([sys.executable, str(TOOL), "stats"],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    src = TOOL.read_text()
    for banned in ("import requests", "import yaml", "from pydantic", "import click"):
        assert banned not in src
