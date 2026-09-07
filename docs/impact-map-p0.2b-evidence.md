# P0.2b evidence — impact map currency

All observations below are local unless explicitly described as hosted CI. Commands shown as
`python3` used the repo virtualenv; pytest ran through that same interpreter with
`-m pytest`. Paths in captured output are normalized to `$ROOT`, `$TMPDIR`, and
`$VENV`; synthetic temporary usernames are normalized to `fixture-user`.
No network access is used by these tests. Base: `e8c512278f`.

Review correction: sections 1–5 and the original scope section below preserve
observations at `ac76e7f388`. Their code line references refer to that revision.
They did **not** establish safe summary destinations, valid currency after a
failed repeated refresh, or merge blocking for generation errors. Those three
claims are corrected explicitly below and in the review-fix evidence section.
The earlier hosted success proves publication for that run only.

HOLD correction: the earlier unqualified contributor-safety claim was false.
At `4a5f972752`, a module relocation or removal could make a historical/current
path mismatch fatal, propagate through the required summary, and authorize an
automatic revert. The committed recall test also lacked a lower bound, and the
write guards did not cover output aliases or repository files outside cps.
The HOLD section at the end records the reproductions and the narrower,
corrected guarantees for B1, B2, B3, F1, and F3. Older passing runs below are
historical observations and did not establish those guarantees.

## 1. Regenerated map and recall

OBSERVED: `python3 scripts/impact_map.py build`, followed by
`python3 scripts/impact_map.py recall`:

```text
wrote $ROOT/state/modernization/impact-map.json: 3396 nodes, 9475 edges, 15299 blind spots

exit code: 0
```
```text
historical recall: 8/10 (80.00%); misses=2

exit code: 0
```

OBSERVED: `generated_from.cps_tree_sha` in `state/modernization/impact-map.json:1`
is `769be5deb55520e94165317132e91af3b38794c6`, equal to `git rev-parse HEAD:cps`.
The source commit is `ab3f6d18c3e556ebf53cd9f88d85227d585c9024`; the recall report
records it at `state/modernization/impact-map-recall.json:7`. The route oracle
and all ten historical cases are unchanged. The score remains 8/10; it is a
constructed case-set result, not a new independent recall measurement.
The retained misses are the TypeScript consumer of the shelf response and the
public-shelf filter reached through an unresolved instance method and keyword branch.

## 2. CI responsibility without a contributor staleness gate

OBSERVED code: `.github/workflows/tests.yml:44` defines the separate regeneration
job on the workflow's existing PR/push/manual triggers (`:3`). It has read-only
contents permission, a five-minute timeout, full history, and artifact upload
with 14-day retention. It requires no package installation for regeneration.
`scripts/impact_map.py:1256` builds fresh outputs and compares both complete JSON
objects. `:1275` records current/stale, and `:1349` returns success for both.
Missing historical evidence failed the standalone job at `:1267`. The guard at
`:1259` only refused an output directory equal to the committed artifact directory;
it did **not** protect inputs or generated outputs from an aliased summary path.
There was no blanket continue-on-error, but the required Test Suite Summary
omitted this job and therefore still admitted a merge after its failure. A failed
repeated refresh could also retain the previous successful currency label beside
new map/recall files. The initial tests below did not exercise these paths. See
review fixes 1–3 for the corrected guarantees and their observed RED/green tests.

The original source-addition test showed advisory drift for that fixture. It did
not establish the contributor constraint for file relocation or removal; both
were later observed to fail on current/historical path mismatches. The HOLD B1
fix below makes those results advisory too. Successful runs publish a fresh
graph/report and visible currency; committed snapshots remain a maintainer
responsibility. CI does not push or open update PRs. Read the checked SHA in a PR
artifact's summary, because the checkout can be a merge candidate.

OBSERVED behavioural test: `tests/unit/test_impact_map.py:240` creates real local Git
history, runs the CLI while current, commits a source addition without updating
artifacts, observes stale with exit zero and intact contributor files, consumes
the generated outputs, then observes current again. It also exercises content drift
with the same cps SHA and missing snapshots. `:293` rejects unavailable history;
`:304` preserves inputs when an output directory would overwrite them.

OBSERVED RED before the command existed:
`python3 -m pytest tests/unit/test_impact_map.py -p no:randomly -k refresh`:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 11 items / 8 deselected / 3 selected

tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates FAILED [ 33%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history FAILED [ 66%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs FAILED [100%]

=================================== FAILURES ===================================
____ test_refresh_publishes_currency_without_requiring_contributor_updates _____
tests/unit/test_impact_map.py:244: in test_refresh_publishes_currency_without_requiring_contributor_updates
    assert result.returncode == 0, result.stderr
E   AssertionError: usage: impact_map.py [-h] [--repo-root REPO_ROOT]
E                          {build,pin-routes,query,recall} ...
E     impact_map.py: error: argument command: invalid choice: 'refresh' (choose from 'build', 'pin-routes', 'query', 'recall')
E     
E   assert 2 == 0
E    +  where 2 = CompletedProcess(args=['$VENV/bin/python', '$ROOT/scripts/impact_map.py', '--repo-root', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_publishes_currenc0/repo', 'refresh', '--output-dir', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_publishes_currenc0/artifacts', '--summary', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_publishes_currenc0/summary.md'], returncode=2, stdout='', stderr="usage: impact_map.py [-h] [--repo-root REPO_ROOT]\n                     {build,pin-routes,query,recall} ...\nimpact_map.py: error: argument command: invalid choice: 'refresh' (choose from 'build', 'pin-routes', 'query', 'recall')\n").returncode
_______________ test_refresh_rejects_unavailable_recall_history ________________
tests/unit/test_impact_map.py:299: in test_refresh_rejects_unavailable_recall_history
    assert "recall evidence unavailable" in result.stderr
E   assert 'recall evidence unavailable' in "usage: impact_map.py [-h] [--repo-root REPO_ROOT]\n                     {build,pin-routes,query,recall} ...\nimpact_map.py: error: argument command: invalid choice: 'refresh' (choose from 'build', 'pin-routes', 'query', 'recall')\n"
E    +  where "usage: impact_map.py [-h] [--repo-root REPO_ROOT]\n                     {build,pin-routes,query,recall} ...\nimpact_map.py: error: argument command: invalid choice: 'refresh' (choose from 'build', 'pin-routes', 'query', 'recall')\n" = CompletedProcess(args=['$VENV/bin/python', '$ROOT/scripts/impact_map.py', '--repo-root', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_rejects_unavailab0/repo', 'refresh', '--output-dir', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_rejects_unavailab0/artifacts', '--summary', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_rejects_unavailab0/summary.md'], returncode=2, stdout='', stderr="usage: impact_map.py [-h] [--repo-root REPO_ROOT]\n                     {build,pin-routes,query,recall} ...\nimpact_map.py: error: argument command: invalid choice: 'refresh' (choose from 'build', 'pin-routes', 'query', 'recall')\n").stderr
______________ test_refresh_refuses_to_overwrite_committed_inputs ______________
tests/unit/test_impact_map.py:307: in test_refresh_refuses_to_overwrite_committed_inputs
    assert "output directory would overwrite committed artifacts" in result.stderr
E   assert 'output directory would overwrite committed artifacts' in "usage: impact_map.py [-h] [--repo-root REPO_ROOT]\n                     {build,pin-routes,query,recall} ...\nimpact_map.py: error: argument command: invalid choice: 'refresh' (choose from 'build', 'pin-routes', 'query', 'recall')\n"
E    +  where "usage: impact_map.py [-h] [--repo-root REPO_ROOT]\n                     {build,pin-routes,query,recall} ...\nimpact_map.py: error: argument command: invalid choice: 'refresh' (choose from 'build', 'pin-routes', 'query', 'recall')\n" = CompletedProcess(args=['$VENV/bin/python', '$ROOT/scripts/impact_map.py', '--repo-root', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_refuses_to_overwr0/repo', 'refresh', '--output-dir', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_refuses_to_overwr0/repo/state/modernization', '--summary', '$TMPDIR/cwng-pytest/12359/pytest-of-fixture-user/pytest-0/test_refresh_refuses_to_overwr0/summary.md'], returncode=2, stdout='', stderr="usage: impact_map.py [-h] [--repo-root REPO_ROOT]\n                     {build,pin-routes,query,recall} ...\nimpact_map.py: error: argument command: invalid choice: 'refresh' (choose from 'build', 'pin-routes', 'query', 'recall')\n").stderr
============================= slowest 10 durations =============================
64.93s setup    tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs
12.58s setup    tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history
11.33s call     tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs
1.92s setup    tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
1.02s call     tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history
0.22s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
0.01s teardown tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs

(2 durations < 0.005s hidden.  Use -vv to show these durations.)
=========================== short test summary info ============================
FAILED tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history
FAILED tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs
================== 3 failed, 8 deselected in 93.38s (0:01:33) ==================

exit code: 1
```

OBSERVED real-tree CLI:
`python3 scripts/impact_map.py refresh --output-dir tmp/p0.2b/ci-current --summary tmp/p0.2b/ci-summary.md`:

```text
## Impact map currency

Committed artifacts: **current**. Fresh artifacts are attached to this CI run.
Staleness is advisory; contributors do not need to regenerate or commit these files.

Checked commit: `5dfa76643e1bac3f475c3ea024e5b3261b5dd70c`
Current cps tree: `769be5deb55520e94165317132e91af3b38794c6`
Committed map cps tree: `769be5deb55520e94165317132e91af3b38794c6`

- `impact-map.json`: current
- `impact-map-recall.json`: current

Curated recall: **8/10 (80.00%)**; misses=2. This constructed case set is not an independent measurement.
- Miss `430601d6a58012dc5e8017431feed25f1b0fe38c`: `frontend/src/pages/Shelf.tsx` → `cps.api.shelves:shelf_detail`: affected_site_not_present_in_current_map
- Miss `9dc72ed57e328855b9d19653d831eeee7abea08b`: `cps.api.shelves:shelf_detail` → `cps.db:public_shelf_book_filter`: The dependency crosses an instance-method call and keyword-controlled branch; methods are folded into a class node and the receiver call is unresolved.

exit code: 0
```

OBSERVED: `actionlint .github/workflows/tests.yml` produced no output and exited 0.
Hosted workflow scheduling and artifact download are not established by this local command.

## 3. Recall improvements are accepted without dropping evidence

OBSERVED code: `tests/unit/test_impact_map.py:313` retains report reproducibility,
at least eight distinct historical commits, all declared cases in order, hit/miss
and percentage accounting, reasons for misses, commit availability, and evidence paths.
That replacement removed both the upper pin and the lower bound. Preserving
case retention and self-consistency was insufficient: the later HOLD reproduction
passed all 27 tests at 0/10 after regenerating a matching report. B3 restores an
eight-hit minimum while accepting improvements; see the HOLD evidence below.

OBSERVED RED/GREEN: an in-memory probe added one hypothetical resolved edge to the
last historical case and evaluated the same ten cases. The existing acceptance test
read that map and its regenerated report through an in-memory loader. This is a
SIMULATED resolver improvement, not a claim that the production generator resolves
that dependency. No artifacts or case files were edited by the probe.

Before removing the exact score assertions:

```text
SIMULATED improvement: 9/10; misses=1
Traceback (most recent call last):
  File "$ROOT/.local/p0.2b/improved_recall_probe.py", line 30, in <module>
    module.test_committed_recall_report_is_reproducible_and_keeps_misses()
  File "$ROOT/tests/unit/test_impact_map.py", line 321, in test_committed_recall_report_is_reproducible_and_keeps_misses
    assert observed["hits"] == 8
           ^^^^^^^^^^^^^^^^^^^^^
AssertionError

exit code: 1
```

After replacing the exact score assertions with accounting:

```text
SIMULATED improvement: 9/10; misses=1
Improved report accepted; all ten cases retained.

exit code: 0
```

Probe used (run with the repo virtualenv):

```python
import importlib.util
import sys
from pathlib import Path
import pytest
root = Path.cwd()
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("test_impact_probe", root / "tests/unit/test_impact_map.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
tool = module.impact_map
real_load = tool.load_json
data = real_load(root / tool.DEFAULT_MAP)
cases = real_load(root / tool.DEFAULT_CASES)
# Simulate one future resolver improvement without changing history or case selection.
case = cases["cases"][-1]
affected = next(n for n in data["nodes"] if tool.node_matches(n, case["affected_site"]))
changed = next(n for n in data["nodes"] if tool.node_matches(n, case["changed_symbol"]))
data["edges"].append({"source": affected["id"], "target": changed["id"], "kind": "call", "confidence": "exact_import_symbol", "location": {"file": affected["file"], "line": affected["line"], "column": 0}})
report = tool.evaluate_recall(data, cases, root)
print(f"SIMULATED improvement: {report['hits']}/{report['total']}; misses={report['misses']}", flush=True)
assert report["hits"] == 9
# The acceptance test reads a map and its regenerated report, supplied only in memory.
def load(path):
    if path.name == tool.DEFAULT_MAP.name:
        return data
    if path.name == tool.DEFAULT_RECALL.name:
        return report
    return real_load(path)
tool.load_json = load
module.test_committed_recall_report_is_reproducible_and_keeps_misses()
print("Improved report accepted; all ten cases retained.")
```

## 4. Fresh-build mutation checks

OBSERVED code: `tests/unit/test_impact_map.py:178` builds a miniature graph and
checks census conservation, the explicit builtin partition, a real class-coarse
call, and a blind record for each guessed/coarse edge at the same source location.
The committed-snapshot route/conservation check remains at `:336`.

The original audit's M2 removes `class_member_folded_to_class`; M5 drops the builtin
out-of-scope count. Those are missing detection at generator-change time, rather
than defense-in-depth predicates: each removes real census records.

OBSERVED exact-source mutation check: `python3 tmp/p0.2b/source_mutation_probe.py`
loaded the unmodified generator and then each exact original source replacement
from separate temporary files. The real new invariant test passed for the baseline
and failed for both mutants. It did not overwrite or regenerate committed JSON.

```text
SOURCE VARIANT: baseline
PASSED
SOURCE VARIANT: M2_class_coarse_drops_blind_spot
TEST_FAILURE
Traceback (most recent call last):
  File "$ROOT/tmp/p0.2b/source_mutation_probe.py", line 33, in <module>
    test.test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind(data)
  File "$ROOT/tests/unit/test_impact_map.py", line 182, in test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind
    assert counts["call_sites"] == (
           ^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError
SOURCE VARIANT: M5_builtin_calls_dropped
TEST_FAILURE
Traceback (most recent call last):
  File "$ROOT/tmp/p0.2b/source_mutation_probe.py", line 33, in <module>
    test.test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind(data)
  File "$ROOT/tests/unit/test_impact_map.py", line 182, in test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind
    assert counts["call_sites"] == (
           ^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError
Observed: passing source baseline; both exact source mutants fail the fresh invariant.

exit code: 0
```

Reproducible mutation specification (the temporary `mutants-fresh.json`):

```json
[
  {
    "name": "M2_class_coarse_drops_blind_spot",
    "file": "scripts/impact_map.py",
    "old": "                self.add_blind(node, \"class_member_folded_to_class\", [binding.target])",
    "new": "                pass",
    "test": "tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind"
  },
  {
    "name": "M5_builtin_calls_dropped",
    "file": "scripts/impact_map.py",
    "old": "            self.add_out_of_scope(\"builtin_call\")",
    "new": "            pass",
    "test": "tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind"
  }
]
```

Exact-source probe used with that specification:

```python
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import traceback
import pytest
root = Path.cwd()
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
test = load("source_mutation_tests", root / "tests/unit/test_impact_map.py")
source = (root / "scripts/impact_map.py").read_text()
mutants = json.loads((root / "tmp/p0.2b/mutants-fresh.json").read_text())
for index, variant in enumerate([None, *mutants]):
    with tempfile.TemporaryDirectory() as temp, pytest.MonkeyPatch.context() as patch:
        script = Path(temp) / "impact_map.py"
        if variant:
            assert source.count(variant["old"]) == 1
            script.write_text(source.replace(variant["old"], variant["new"], 1))
        else:
            script.write_text(source)
        tool = load("impact_source_variant_" + str(index), script)
        patch.setattr(tool, "git_sha", lambda root: "1" * 40)
        patch.setattr(tool, "git_object_sha", lambda root, revision: "a" * 40)
        repo, oracle = test.miniature_repo(Path(temp))
        data = tool.build_map(repo, oracle)
        print("SOURCE VARIANT:", variant["name"] if variant else "baseline")
        try:
            test.test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind(data)
        except AssertionError:
            if not variant:
                raise
            print("TEST_FAILURE")
            traceback.print_exc(file=sys.stdout)
        else:
            if variant:
                raise RuntimeError("SURVIVED: " + variant["name"])
            print("PASSED")
print("Observed: passing source baseline; both exact source mutants fail the fresh invariant.")
```

OBSERVED committed-seed harness: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3
tests/mutation/mutate.py --backend macos --seed 5dfa76643e --spec
tmp/p0.2b/mutants-fresh.json --timeout 120`:

```text
UNVERIFIED diagnostic backend; committed seed only; shared Git writes UNSUPPORTED
Outside boundary: temporary directories, venv, home, common Git data, Docker, databases, network, ports, caches, services and escaped processes
UNVERIFIED seed=5dfa76643e1bac3f475c3ea024e5b3261b5dd70c
UNVERIFIED TEST_FAILURE: execution checks passed; authority remains unverified
UNVERIFIED observation=1 evidence=8115921fc1d64144ae3e5ff2bc536a4b.json
UNVERIFIED ERROR: provenance REJECTED: pytest probe execution failed (exit=-9, timeout=True, cleanup=phase processes remain or have not been reaped before cleanup deadline)
UNVERIFIED observation=2 evidence=77c07ef588f14d6888fb5e91421e521d.json

exit code: 1
```

The first record, `8115921fc1d64144ae3e5ff2bc536a4b.json`, records collection=0,
baseline=0 (`1 passed`), and mutant=1 (`1 failed`), with the selected invariant's
call outcome `failed` and target provenance `active=true, seen=true, foreign=false`.
The second mutant stopped during provenance checking; it was not a surviving mutant.

Interpretation: both exact source mutants fail the new invariant; each removes one
of eight miniature call sites from its accounting partition. These are real missing
records, not redundant predicates. M2 has a complete committed-seed harness trace here; M5 is recorded below. No authoritative isolation or global mutation score is claimed: the
macOS backend explicitly reports UNVERIFIED and always exits nonzero, including when
it observes a test failure. M5's exact-source failure is independently reproduced
above; remaining harness evidence is recorded below.

OBSERVED M5 committed-seed run completed with the small pytest temporary directory
selected explicitly (plugin autoload disabled for the mutation harness only):

```sh
CWNG_PYTEST_TMP_BASE="$TMPDIR/cwng-p0.2b-pytest" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 tests/mutation/mutate.py --backend macos --seed 5dfa76643e \
  --spec tmp/p0.2b/mutant-builtin.json --timeout 120
```

`mutant-builtin.json` is the M5 entry from the specification above.

```text
UNVERIFIED diagnostic backend; committed seed only; shared Git writes UNSUPPORTED
Outside boundary: temporary directories, venv, home, common Git data, Docker, databases, network, ports, caches, services and escaped processes
UNVERIFIED seed=5dfa76643e1bac3f475c3ea024e5b3261b5dd70c
UNVERIFIED TEST_FAILURE: execution checks passed; authority remains unverified
UNVERIFIED observation=1 evidence=ffa3daf9c83848639e13508c339b44d6.json

exit code: 1
```

OBSERVED phase evidence extracted from `ffa3daf9c83848639e13508c339b44d6.json`:

```json
[
  {
    "phase": "collection",
    "returncode": 0,
    "summary": "1 test collected"
  },
  {
    "phase": "baseline",
    "returncode": 0,
    "summary": "1 passed"
  },
  {
    "phase": "mutant",
    "returncode": 1,
    "summary": "1 failed"
  }
]
```

Both M2 and M5 therefore have passing baselines followed by test-call failures in
the existing committed-seed harness. Neither is an observed survivor. The harness's
macOS isolation guarantee remains UNVERIFIED; its exit 1 here is expected diagnostic
policy, not a remaining test failure in the unmodified unit file.


## 5. Changelog guard

OBSERVED: the diff touches `scripts/impact_map.py`, a non-exempt path, so
`changelog.d/impact-map-currency.md:1` is included.
`python3 scripts/check_changelog_diff.py origin/main HEAD` (the guard requires two refs):

```text
CHANGELOG integrity guard passed: the entry requirement is satisfied or every changed path is non-shipping, and no PR-authored release structure was lost.

exit code: 0
```

## Two consecutive full unit-file runs

OBSERVED: both commands below ran with ordinary plugin autoload enabled, no randomly
plugin, no xdist, no retries, and no code changes between the runs. Both ran against
the regenerated artifact files. Neither skipped nor deselected a test.

`python3 -m pytest tests/unit/test_impact_map.py -p no:randomly` — first run:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 11 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  9%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [ 18%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [ 27%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [ 36%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [ 45%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [ 54%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [ 63%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [ 72%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 81%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses PASSED [ 90%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [100%]

============================= slowest 10 durations =============================
8.85s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
5.90s setup    tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history
4.90s setup    tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
4.33s setup    tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs
4.13s call     tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses
1.20s call     tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history
0.91s setup    tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses
0.29s call     tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs
0.20s call     tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor
0.06s call     tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json
============================= 11 passed in 34.79s ==============================

exit code: 0
```

Same command — second consecutive run:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 11 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  9%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [ 18%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [ 27%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [ 36%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [ 45%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [ 54%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [ 63%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [ 72%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 81%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses PASSED [ 90%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [100%]

============================= slowest 10 durations =============================
13.78s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
4.57s setup    tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
2.97s setup    tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs
2.94s setup    tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history
2.65s call     tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses
1.02s call     tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history
0.26s call     tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs
0.17s call     tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor
0.15s setup    tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses
0.01s call     tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json
============================= 11 passed in 29.16s ==============================

exit code: 0
```

## Scope and unsuccessful attempts

OBSERVED: a preliminary run exposed two synthetic-fixture mistakes: a module-qualified
class call did not take the coarse-import resolver path, and a root commit had no
parent for historical diff evidence. The fixtures were corrected before the two
full green runs. Earlier refresh setup also hit the local identity hook when the
synthetic repository used a real project identity; fixtures now use `fixture`.
These setup failures are not counted as regression-test RED evidence.

OBSERVED: the first mutation attempt timed out in the provenance probe; another
attempt timed out in Git. A third refused its bad-fixture baseline. These are
harness errors, not caught mutants or survivors. The retained macOS backend warns
that its process isolation is diagnostic and always exits nonzero.

Not done: no cps implementation changes, no resolver accuracy expansion, no new
route oracle, no changes to historical case selection, no independent held-out
recall replication, no application/UI/container test, no merge or release.


## Review fixes: summary corruption, failed refresh reuse, and required gate

OBSERVED: new regression tests were committed before implementation in
`b6ff4faf0c`; fixes and user documentation were committed in `a737b01b0d`.
Existing tests and assertions were retained. References in this section use the
fixed source at `a737b01b0d` unless a historical revision is explicitly named.

### 1. Reject summary aliases before any write

OBSERVED at the earlier revision: `scripts/impact_map.py:1262` rejected a
summary alias of the committed map/recall, route oracle, historical cases, parsed
Python inputs, or output files. That was summary-only protection; it did not
reject output links or summary destinations naming repository files outside cps.
The HOLD F1 fix below extends both preflight and the writer.
Resolved destination comparison catches path and symbolic-link aliases, including
missing outputs; `Path.samefile` also catches hard links (`:1272`). Rejection
precedes currency invalidation and any publication (`:1277`).

OBSERVED behavioural test: `tests/unit/test_impact_map.py:322` runs the real CLI
against thirteen destinations/alias forms and snapshots every protected file.
A changed source makes a late check observable even if it eventually rejects
the path. Each bad destination must fail without changing any protected bytes;
the missing-output case must not even create the output directory.
All thirteen were seen RED before the fix, then GREEN.

### 2. Invalidate currency before a repeated refresh

OBSERVED code: `scripts/impact_map.py:1280` removes the previous currency file
after destination validation and before input reads/build/evaluation. The new
currency is written last, after successful validation and summary writing
(`:1322`). This deliberately uses invalidation: failed attempts may leave
diagnostic map/recall files, but no successful currency label. It does not
provide atomic directory replacement or coordinate concurrent writers.

OBSERVED behavioural test: `tests/unit/test_impact_map.py:360` first refreshes
revision A successfully, commits revision B, then retries in the same directory
with an unavailable historical commit, malformed cases, or a malformed oracle.
All three failures previously retained A's currency label. Each now removes it,
preserves the previous summary without appending a success claim, and can recover
with coherent B provenance after the input is repaired. All three were seen RED
before the fix, then GREEN.

OBSERVED combined RED command (before either refresh fix):
`python3 -m pytest tests/unit/test_impact_map.py -p no:randomly -k 'summary_aliases or failed_refresh_invalidates' --tb=line`.
This captures all sixteen new parameter cases; `--tb=line` keeps the actual
failure message per case. Any abbreviated value representation is pytest's own. Trailing whitespace
in copied output is trimmed for Markdown; command results are unchanged.

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 27 items / 11 deselected / 16 selected

tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] FAILED [  6%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] FAILED [ 12%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] FAILED [ 18%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] FAILED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] FAILED [ 31%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] FAILED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] FAILED [ 43%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] FAILED [ 50%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] FAILED [ 56%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] FAILED [ 62%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] FAILED [ 68%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] FAILED [ 75%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] FAILED [ 81%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] FAILED [ 87%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] FAILED [ 93%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] FAILED [100%]

=================================== FAILURES ===================================
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."live_route_count":1,"module":"cps.provider","name":"imported","reached_from_live_route":true}],"schema_version":1}\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."live_route_count":1,"module":"cps.provider","name":"imported","reached_from_live_route":true}],"schema_version":1}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (669 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."impact-map-recall.json": false,\n    "impact-map.json": true\n  },\n  "schema_version": 1,\n  "status": "stale"\n}\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...pact-map-recall.json": false,\n    "impact-map.json": false\n  },\n  "schema_version": 1,\n  "status": "current"\n}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (666 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."live_route_count":1,"module":"cps.provider","name":"imported","reached_from_live_route":true}],"schema_version":1}\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."live_route_count":1,"module":"cps.provider","name":"imported","reached_from_live_route":true}],"schema_version":1}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (669 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."impact-map-recall.json": false,\n    "impact-map.json": true\n  },\n  "schema_version": 1,\n  "status": "stale"\n}\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...pact-map-recall.json": false,\n    "impact-map.json": false\n  },\n  "schema_version": 1,\n  "status": "current"\n}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (667 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/..._path\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."impact-map-recall.json": false,\n    "impact-map.json": true\n  },\n  "schema_version": 1,\n  "status": "stale"\n}\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...pact-map-recall.json": false,\n    "impact-map.json": false\n  },\n  "schema_version": 1,\n  "status": "current"\n}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (667 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 6 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...655c4c6a9c4797d350e681a4e4dae404fb504dd`: `cps.app:dynamic` \xe2\x86\x92 `cps.provider:Worker`: no_static_call_path\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."live_route_count":1,"module":"cps.provider","name":"imported","reached_from_live_route":true}],"schema_version":1}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (666 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."impact-map-recall.json": false,\n    "impact-map.json": true\n  },\n  "schema_version": 1,\n  "status": "stale"\n}\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...pact-map-recall.json": false,\n    "impact-map.json": false\n  },\n  "schema_version": 1,\n  "status": "current"\n}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (666 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 6 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...c0f74e15cd9c9d1c0ddb97da4a0ec4ab5ef7027`: `cps.app:dynamic` \xe2\x86\x92 `cps.provider:Worker`: no_static_call_path\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...pact-map-recall.json": false,\n    "impact-map.json": false\n  },\n  "schema_version": 1,\n  "status": "current"\n}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (665 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...25a554923533bec14adab41886d94a80ed48e3c`: `cps.app:dynamic` \xe2\x86\x92 `cps.provider:Worker`: no_static_call_path\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."live_route_count":1,"module":"cps.provider","name":"imported","reached_from_live_route":true}],"schema_version":1}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (669 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 6 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...4322c18d79f5eead4f4cee01be9b9e97d80d3e9`: `cps.app:dynamic` \xe2\x86\x92 `cps.provider:Worker`: no_static_call_path\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...pact-map-recall.json": false,\n    "impact-map.json": false\n  },\n  "schema_version": 1,\n  "status": "current"\n}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (665 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...4757b3729f92803d297826e84a9d82234df0f4f`: `cps.app:dynamic` \xe2\x86\x92 `cps.provider:Worker`: no_static_call_path\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."changed_symbol": "cps.provider:Worker",\n      "commit": "94757b3729f92803d297826e84a9d82234df0f4f"\n    }\n  ]\n}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (667 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 6 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa...5941f7edc960945fa0457f3135a16fbf842b2f6`: `cps.app:dynamic` \xe2\x86\x92 `cps.provider:Worker`: no_static_call_path\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."live_route_count":1,"module":"cps.provider","name":"imported","reached_from_live_route":true}],"schema_version":1}\n'}
      {PosixPath('$TMPDIR/a...

      ...Full output truncated (666 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: summary collision changed protected bytes before rejection
    assert {PosixPath('/...run()\n", ...} == {PosixPath('/...run()\n", ...}

      Omitting 5 identical items, use -vv to show
      Differing items:
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summa..."live_route_count":1,"module":"cps.provider","name":"imported","reached_from_live_route":true}],"schema_version":1}\n'} != {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_summary_a12/artifacts/impact-map.json'): None}
      {PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_refresh_rejects_s...

      ...Full output truncated (337 lines hidden), use '-vv' to show
$ROOT/tests/unit/test_impact_map.py:352: AssertionError: summary collision changed protected bytes before rejection
E   AssertionError: failed refresh retained a successful currency label from the previous revision
    assert not True
     +  where True = exists()
     +    where exists = PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_failed_refresh_invalidate0/artifacts/impact-map-currency.json').exists
$ROOT/tests/unit/test_impact_map.py:386: AssertionError: failed refresh retained a successful currency label from the previous revision
E   AssertionError: failed refresh retained a successful currency label from the previous revision
    assert not True
     +  where True = exists()
     +    where exists = PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_failed_refresh_invalidate1/artifacts/impact-map-currency.json').exists
$ROOT/tests/unit/test_impact_map.py:386: AssertionError: failed refresh retained a successful currency label from the previous revision
E   AssertionError: failed refresh retained a successful currency label from the previous revision
    assert not True
     +  where True = exists()
     +    where exists = PosixPath('$TMPDIR/cwng-pytest/82705/pytest-of-fixture-user/pytest-0/test_failed_refresh_invalidate2/artifacts/impact-map-currency.json').exists
$ROOT/tests/unit/test_impact_map.py:386: AssertionError: failed refresh retained a successful currency label from the previous revision
============================= slowest 10 durations =============================
17.50s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct]
10.36s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct]
10.10s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct]
9.99s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct]
8.91s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct]
8.90s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink]
8.24s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink]
8.04s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct]
7.94s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct]
7.77s call     tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct]
=========================== short test summary info ============================
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent]
FAILED tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
FAILED tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases]
FAILED tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
================ 16 failed, 11 deselected in 199.84s (0:03:19) =================

exit code: 1
```

OBSERVED GREEN, same command after the fixes:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 27 items / 11 deselected / 16 selected

tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] PASSED [  6%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] PASSED [ 12%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] PASSED [ 18%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] PASSED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] PASSED [ 31%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] PASSED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] PASSED [ 43%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] PASSED [ 50%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] PASSED [ 56%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] PASSED [ 62%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] PASSED [ 68%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] PASSED [ 75%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] PASSED [ 81%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] PASSED [ 87%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] PASSED [ 93%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] PASSED [100%]

============================= slowest 10 durations =============================
2.13s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct]
2.09s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
1.96s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases]
1.86s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
1.84s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct]
1.84s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct]
1.82s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct]
1.78s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink]
1.78s setup    tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
1.78s setup    tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases]
====================== 16 passed, 11 deselected in 43.56s ======================

exit code: 0
```

### 3. Require impact-map success in the merge summary

OBSERVED code: `.github/workflows/tests.yml:1082` now declares:

```yaml
needs: [fast-tests, frontend-build, impact-map, integration-tests, e2e-tests, changed_paths]
```

The required summary keeps `if: always()` (`:1083`) and rejects all non-success
impact-map results on every trigger (`:1159`):

```bash
if [[ "${{ needs.impact-map.result }}" != "success" ]]; then
  echo "❌ Impact map did not succeed"
  echo "   Actual result: ${{ needs.impact-map.result }}"
  exit 1
fi
```

OBSERVED: the executable gate now returns failure for impact-map failure,
cancellation, or skipping. Previously these states returned success, so branch
protection relying on Test Suite Summary could merge them. This new gate also
exposed the B1 classification bug: a current/historical path mismatch could now
block a refactor, and the Impact Map failure could trigger automatic revert.
The HOLD B1/B2 fixes below correct those paths. Advisory staleness
continues to exit zero in `scripts/impact_map.py:1369` and pass the gate.

OBSERVED behavioural test: `tests/unit/test_summary_gate_requires_success.py:101`
executes the workflow's actual shell for four job results across five trigger
forms (PR, main/dev push, version tag, manual dispatch). Its renderer (`:48`)
uses the YAML dependency list to model which job results are available. The
fifteen non-success cases were RED on the original workflow. Adding only the
predicate then made all five success controls RED because the undeclared job's
result was unavailable. Adding the dependency made all twenty cases GREEN.
No existing gate assertion was weakened; the full file's nineteen earlier cases
also pass. PyYAML was already declared in `pyproject.toml:81`; no dependency was
added.

OBSERVED RED on the original summary:
`python3 -m pytest tests/unit/test_summary_gate_requires_success.py -p no:randomly -k impact_map --tb=line`:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 39 items / 19 deselected / 20 selected

tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge] PASSED [  5%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/main] PASSED [ 10%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/dev] PASSED [ 15%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/tags/v1.0.0] PASSED [ 20%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-workflow_dispatch-refs/heads/main] PASSED [ 25%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-pull_request-refs/pull/1/merge] FAILED [ 30%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/main] FAILED [ 35%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/dev] FAILED [ 40%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/tags/v1.0.0] FAILED [ 45%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-workflow_dispatch-refs/heads/main] FAILED [ 50%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-pull_request-refs/pull/1/merge] FAILED [ 55%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/main] FAILED [ 60%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/dev] FAILED [ 65%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/tags/v1.0.0] FAILED [ 70%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-workflow_dispatch-refs/heads/main] FAILED [ 75%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-pull_request-refs/pull/1/merge] FAILED [ 80%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/main] FAILED [ 85%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/dev] FAILED [ 90%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/tags/v1.0.0] FAILED [ 95%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-workflow_dispatch-refs/heads/main] FAILED [100%]

=================================== FAILURES ===================================
E   AssertionError: pull_request with impact-map=failure: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: pull_request with impact-map=failure: expected exit 1, got 0
E   AssertionError: push with impact-map=failure: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      true
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=failure: expected exit 1, got 0
E   AssertionError: push with impact-map=failure: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=failure: expected exit 1, got 0
E   AssertionError: push with impact-map=failure: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=failure: expected exit 1, got 0
E   AssertionError: workflow_dispatch with impact-map=failure: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: workflow_dispatch with impact-map=failure: expected exit 1, got 0
E   AssertionError: pull_request with impact-map=skipped: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: pull_request with impact-map=skipped: expected exit 1, got 0
E   AssertionError: push with impact-map=skipped: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      true
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=skipped: expected exit 1, got 0
E   AssertionError: push with impact-map=skipped: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=skipped: expected exit 1, got 0
E   AssertionError: push with impact-map=skipped: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=skipped: expected exit 1, got 0
E   AssertionError: workflow_dispatch with impact-map=skipped: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: workflow_dispatch with impact-map=skipped: expected exit 1, got 0
E   AssertionError: pull_request with impact-map=cancelled: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: pull_request with impact-map=cancelled: expected exit 1, got 0
E   AssertionError: push with impact-map=cancelled: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      true
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=cancelled: expected exit 1, got 0
E   AssertionError: push with impact-map=cancelled: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=cancelled: expected exit 1, got 0
E   AssertionError: push with impact-map=cancelled: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=cancelled: expected exit 1, got 0
E   AssertionError: workflow_dispatch with impact-map=cancelled: expected exit 1, got 0
      Fast Tests:        success
      Frontend Build:    success
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ✅ Test suite completed

    assert 0 == 1
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: workflow_dispatch with impact-map=cancelled: expected exit 1, got 0
============================= slowest 10 durations =============================
0.55s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/main]
0.53s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/tags/v1.0.0]
0.46s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/tags/v1.0.0]
0.46s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-workflow_dispatch-refs/heads/main]
0.46s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/tags/v1.0.0]
0.46s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/dev]
0.45s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/main]
0.44s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge]
0.42s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-pull_request-refs/pull/1/merge]
0.41s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-workflow_dispatch-refs/heads/main]
=========================== short test summary info ============================
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-pull_request-refs/pull/1/merge]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/main]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/dev]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/tags/v1.0.0]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-workflow_dispatch-refs/heads/main]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-pull_request-refs/pull/1/merge]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/main]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/dev]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/tags/v1.0.0]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-workflow_dispatch-refs/heads/main]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-pull_request-refs/pull/1/merge]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/main]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/dev]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/tags/v1.0.0]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-workflow_dispatch-refs/heads/main]
================= 15 failed, 5 passed, 19 deselected in 9.91s ==================

exit code: 1
```

OBSERVED RED after adding the predicate but before its dependency:
`python3 -m pytest tests/unit/test_summary_gate_requires_success.py -p no:randomly -k 'impact_map and success-' --tb=line`:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 39 items / 34 deselected / 5 selected

tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge] FAILED [ 20%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/main] FAILED [ 40%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/dev] FAILED [ 60%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/tags/v1.0.0] FAILED [ 80%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-workflow_dispatch-refs/heads/main] FAILED [100%]

=================================== FAILURES ===================================
E   AssertionError: pull_request with impact-map=success: expected exit 0, got 1
      Fast Tests:        success
      Frontend Build:    success
      Impact map:
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ❌ Impact map did not succeed
         Actual result:

    assert 1 == 0
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: pull_request with impact-map=success: expected exit 0, got 1
E   AssertionError: push with impact-map=success: expected exit 0, got 1
      Fast Tests:        success
      Frontend Build:    success
      Impact map:
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      true
      ❌ Impact map did not succeed
         Actual result:

    assert 1 == 0
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=success: expected exit 0, got 1
E   AssertionError: push with impact-map=success: expected exit 0, got 1
      Fast Tests:        success
      Frontend Build:    success
      Impact map:
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ❌ Impact map did not succeed
         Actual result:

    assert 1 == 0
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=success: expected exit 0, got 1
E   AssertionError: push with impact-map=success: expected exit 0, got 1
      Fast Tests:        success
      Frontend Build:    success
      Impact map:
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ❌ Impact map did not succeed
         Actual result:

    assert 1 == 0
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: push with impact-map=success: expected exit 0, got 1
E   AssertionError: workflow_dispatch with impact-map=success: expected exit 0, got 1
      Fast Tests:        success
      Frontend Build:    success
      Impact map:
      Integration Tests: success
      E2E Tests:         success
      Is tier-2 PR:      false
      Is build PR:       false
      Is frontend PR:    false
      Is concurrency PR: false
      Is main push:      false
      ❌ Impact map did not succeed
         Actual result:

    assert 1 == 0
$ROOT/tests/unit/test_summary_gate_requires_success.py:108: AssertionError: workflow_dispatch with impact-map=success: expected exit 0, got 1
============================= slowest 10 durations =============================
0.22s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/main]
0.22s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/dev]
0.20s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge]
0.19s setup    tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge]
0.18s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/tags/v1.0.0]
0.18s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-workflow_dispatch-refs/heads/main]

(4 durations < 0.005s hidden.  Use -vv to show these durations.)
=========================== short test summary info ============================
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/main]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/dev]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/tags/v1.0.0]
FAILED tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-workflow_dispatch-refs/heads/main]
======================= 5 failed, 34 deselected in 1.86s =======================

exit code: 1
```

OBSERVED GREEN, full summary test file:
`python3 -m pytest tests/unit/test_summary_gate_requires_success.py -p no:randomly`:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 39 items

tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge] PASSED [  2%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/main] PASSED [  5%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/dev] PASSED [  7%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/tags/v1.0.0] PASSED [ 10%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-workflow_dispatch-refs/heads/main] PASSED [ 12%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-pull_request-refs/pull/1/merge] PASSED [ 15%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/main] PASSED [ 17%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/dev] PASSED [ 20%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/tags/v1.0.0] PASSED [ 23%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-workflow_dispatch-refs/heads/main] PASSED [ 25%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-pull_request-refs/pull/1/merge] PASSED [ 28%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/main] PASSED [ 30%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/dev] PASSED [ 33%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/tags/v1.0.0] PASSED [ 35%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-workflow_dispatch-refs/heads/main] PASSED [ 38%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-pull_request-refs/pull/1/merge] PASSED [ 41%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/main] PASSED [ 43%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/dev] PASSED [ 46%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/tags/v1.0.0] PASSED [ 48%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-workflow_dispatch-refs/heads/main] PASSED [ 51%]
tests/unit/test_summary_gate_requires_success.py::test_positive_control_all_success_passes PASSED [ 53%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[failure] PASSED [ 56%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[skipped] PASSED [ 58%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[cancelled] PASSED [ 61%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_the_other_hard_gated_lanes_too[fast-tests-kwargs0] PASSED [ 64%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_the_other_hard_gated_lanes_too[frontend-build-kwargs1] PASSED [ 66%]
tests/unit/test_summary_gate_requires_success.py::test_integration_stays_advisory_on_main PASSED [ 69%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[failure] PASSED [ 71%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[skipped] PASSED [ 74%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[cancelled] PASSED [ 76%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[failure] PASSED [ 79%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[skipped] PASSED [ 82%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[cancelled] PASSED [ 84%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_changed_paths_success[skipped] PASSED [ 87%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_changed_paths_success[cancelled] PASSED [ 89%]
tests/unit/test_summary_gate_requires_success.py::test_non_frontend_pr_still_passes_with_e2e_skipped PASSED [ 92%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[failure] PASSED [ 94%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[skipped] PASSED [ 97%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[cancelled] PASSED [100%]

============================= slowest 10 durations =============================
0.14s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/dev]
0.14s call     tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[cancelled]
0.14s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/dev]
0.14s call     tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/main]
0.14s call     tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[cancelled]
0.14s call     tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[skipped]
0.13s call     tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[failure]
0.13s call     tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[failure]
0.13s call     tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[skipped]
0.12s call     tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[cancelled]
============================== 39 passed in 4.84s ==============================

exit code: 0
```


OBSERVED active branch rules:
`gh api repos/new-usemame/Calibre-Web-NextGen/rules/branches/main --jq '[.[] | select(.type == "required_status_checks") | {type,parameters}]'`:

```json
[{"parameters":{"do_not_enforce_on_create":false,"required_status_checks":[{"context":"validate-author"},{"context":"Fast Tests (Smoke + Unit)"},{"context":"Test Suite Summary"}],"strict_required_status_checks_policy":true},"type":"required_status_checks"}]
```

Exit code: 0. The older branch-protection endpoint returned `Branch not protected`
(HTTP 404); the active rules endpoint above supplies the required-check evidence.
This read was outside test execution. No intentionally broken hosted run or
actual merge was attempted; non-success gating is observed through execution of
the workflow shell locally, and the active required-check policy is observed
through the API.

### Regeneration after the review fixes

OBSERVED: `python3 scripts/impact_map.py build`, then
`python3 scripts/impact_map.py recall`:

```text
wrote $ROOT/state/modernization/impact-map.json: 3396 nodes, 9475 edges, 15299 blind spots

exit code: 0
```

```text
historical recall: 8/10 (80.00%); misses=2

exit code: 0
```

OBSERVED: `git status --short` showed only this evidence document changed;
build and recall reproduced both committed artifacts without a diff.


### Two consecutive complete impact-map runs

OBSERVED: after the fixes, two consecutive invocations of
`python3 -m pytest tests/unit/test_impact_map.py -p no:randomly` completed with
all 27 cases passing and no skips or deselections. The source and tests were
unchanged between these runs; only evidence documentation was committed.

First run:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 27 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  3%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [  7%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [ 11%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [ 14%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [ 18%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [ 22%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [ 29%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 33%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] PASSED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] PASSED [ 40%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] PASSED [ 44%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] PASSED [ 48%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] PASSED [ 51%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] PASSED [ 55%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] PASSED [ 59%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] PASSED [ 62%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] PASSED [ 66%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] PASSED [ 70%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] PASSED [ 74%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] PASSED [ 77%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] PASSED [ 81%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] PASSED [ 85%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] PASSED [ 88%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] PASSED [ 92%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses PASSED [ 96%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [100%]

============================= slowest 10 durations =============================
5.40s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
3.09s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent]
3.02s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct]
2.50s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct]
2.49s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct]
2.28s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink]
2.23s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
2.19s setup    tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
2.13s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink]
2.04s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink]
======================== 27 passed in 65.21s (0:01:05) =========================

exit code: 0
```

Second consecutive run:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 27 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  3%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [  7%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [ 11%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [ 14%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [ 18%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [ 22%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [ 29%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 33%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] PASSED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] PASSED [ 40%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] PASSED [ 44%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] PASSED [ 48%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] PASSED [ 51%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] PASSED [ 55%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] PASSED [ 59%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] PASSED [ 62%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] PASSED [ 66%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] PASSED [ 70%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] PASSED [ 74%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] PASSED [ 77%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] PASSED [ 81%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] PASSED [ 85%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] PASSED [ 88%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] PASSED [ 92%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses PASSED [ 96%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [100%]

============================= slowest 10 durations =============================
5.99s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
3.35s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
2.67s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
2.51s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent]
2.50s setup    tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
2.39s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases]
2.20s call     tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses
2.16s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct]
2.10s setup    tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
2.05s setup    tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
======================== 27 passed in 62.52s (0:01:02) =========================

exit code: 0
```

### Changelog and workflow validation

OBSERVED: `scripts/` remains non-exempt. The fragment at
`changelog.d/impact-map-currency.md:3` now describes the required error gate,
summary collision rejection, and removal of obsolete currency metadata.
`docs/impact-map.md:56` describes the required gate and `:69` explains destination
protection and the local invalidation protocol.

OBSERVED: `python3 scripts/check_changelog_diff.py origin/main HEAD`:

```text
CHANGELOG integrity guard passed: the entry requirement is satisfied or every changed path is non-shipping, and no PR-authored release structure was lost.

exit code: 0
```

OBSERVED: `actionlint .github/workflows/tests.yml` produced no output;
**exit code: 0**. `git diff --check` also exited 0 after trimming trailing
whitespace in the copied pytest output. No runtime dependency was added.

Not done in this review-fix pass: no cps implementation changes, case/oracle
changes, mutation rerun, application/UI/container testing, deliberately failing
hosted CI run, merge, or release. The original mutation observations remain
historical evidence; they are not presented as a new measurement. Reused output
directories are invalidated on failure, not replaced atomically. Concurrent
refreshes must use separate directories.


## HOLD fixes — B1, B2, B3, F1, and F3

OBSERVED: `811ffbe582` records the new tests before fixes. All sixteen new
parameter cases were seen RED. `c691a5a822` implements the fixes; `b8441b65ae`
keeps the improvement control open to higher recall and uses workflow-derived
job names in the positive revert controls. Existing tests and assertions from
`4a5f972752` are retained, with the eight-hit floor added. Code references in
this section refer to `b8441b65ae` unless a different revision is stated.
Commands use the repo virtualenv; all test/reproduction Git history is local.
Local paths, temporary usernames, and the clone's origin setting are normalized;
trailing whitespace in captured output is trimmed.

### B1. Current/historical path mismatches are measured misses

OBSERVED code: `scripts/impact_map.py:1323` rejects an empty case set and
`:1325` rejects an unavailable historical commit. It no longer raises for
`evidence_paths_present: false`. Evaluation retains the false predicate and
`historical_diff_does_not_touch_declared_sites` miss reason; publication reports
them in the summary. The unchanged required summary still rejects a failed job
(`.github/workflows/tests.yml:1159`), but these evaluated misses now exit zero.

OBSERVED test: `tests/unit/test_impact_map.py:316` commits a module-to-package
move or removes both modules named in a synthetic historical case. It requires
false evidence-path predicates, available commits, miss reasons in the report
and summary, exit zero, stale currency, and unchanged committed snapshots.
Both cases were RED on the prior implementation and GREEN after the fix.
The existing unavailable-history test (`:295`) remains fatal and passing.

OBSERVED full-tree reproductions: a local sparse clone of `4a5f972752` shared
read-only Git objects with the supplied worktree. Each scenario reset only that
disposable clone to the base, then committed exactly the stated cps move/removal.
The fixed phase copied the revised generator/tests/skill into that clone; the
source changes, case set, oracle, and committed map were otherwise unchanged.
No cps file in the supplied worktree was changed. The commands below include the
exact `git mv` and `git rm`, refresh outputs, miss details, and exit codes.

Both baseline scenarios exited 1 at 7/10. Both fixed scenarios exit 0 at 7/10,
with the added miss caused by the current/historical path mismatch. The source
relocation is reported as a measurement result, not a shallow-clone error.
These are bounded observed refactor cases; the prior unqualified statement that
any cps-only PR could never be blocked was false and is withdrawn.

### B2. Impact Map never authorizes an automatic revert

OBSERVED code: `.github/workflows/auto-revert.yml:145` adds the exact job name
to the existing exclusion predicate:

```jq
| select(.name != "E2E Tests (SPA)" and .name != "Test Suite Summary"
         and .name != "Impact Map (regeneration + currency)")
```

The reason is documented at `:72`: advisory drift/misses are measurements;
unavailable history or generator/input failures mean the measurement did not
complete. They do not establish a product regression caused by the commit.
The required summary still exposes those failures, while auto-revert must not
undo an application refactor to repair tooling or infrastructure.

OBSERVED test: `tests/unit/test_summary_gate_requires_success.py:96` executes
the complete triage shell and its real jq, using job names read from the Test
Suite workflow. Only external Git/GitHub reads are stubbed. Impact Map failure,
with or without a consequent summary failure, previously emitted `revert=true`;
both now emit `revert=false`. The same tests retain the SPA exclusion and require
`revert=true` for the actual fast-test, frontend-build, and integration job names.
No live revert PR was opened and no hosted failure was injected.

### B3. A matching report cannot hide collapsed committed recall

OBSERVED code: `tests/unit/test_impact_map.py:576` restores the one-sided floor:

```python
assert observed["hits"] >= 8, "committed recall fell below the eight-case floor"
```

Reproducibility, retained cases, historical availability, miss reasons, and
accounting remain checked. This floor applies to the committed map and report,
not the freshly measured source tree of a contributor PR. That distinction is
why B1 can report 7/10 and succeed while a gutted committed map fails the suite.

OBSERVED test: `tests/unit/test_impact_map.py:446` removes call edges and
regenerates a matching report in memory, then invokes the actual committed-report
test. Before the floor it failed with `DID NOT RAISE`; after the floor it checks
that collapse is rejected and that an additional valid path passes at at least
nine hits. The improvement control has no upper bound.

OBSERVED judge-style collapse: retaining only imports reduces edges from 9,475
to exactly 1,355. `recall` regenerates a matching committed report at 0/10. The
old file still passed all 27 tests. With the fix, the complete file is RED:
`assert 0 >= 8` fails the committed-report test. The improvement control also
fails because adding one edge to the already-gutted graph only recovers one hit.
Both failures describe the corrupted artifact; this is not a harness error.
The unmodified supplied-worktree artifact remains 8/10 and is used for the final
consecutive GREEN runs below.

### F1. Guard outputs and repository inputs at publication

OBSERVED code: `scripts/impact_map.py:1296` enumerates tracked repository files,
including the generator/tests, alongside explicit evidence inputs, parsed cps
files, and the executing generator. `:1305` protects each destination from the
inputs, peer outputs, and summary. All destinations are checked before any
currency invalidation or publication.

The writer at `scripts/impact_map.py:128` repeats alias checks at publication
and writes a temporary sibling before replacing the destination. JSON writes
therefore do not follow symbolic/hard links into another file even outside
refresh; the shared summary writer uses the same checks. Individual-file
replacement does not make the whole directory atomic or coordinate writers.

OBSERVED tests: output aliases (`tests/unit/test_impact_map.py:346`), generator
and test-file summary destinations (`:366`), aliases installed during generation
so preflight cannot see them (`:388`), and direct JSON-writer symbolic/hard links
(`:410`) were all RED before the fix and GREEN after it. The output-alias test
was strengthened with a source addition and rerun RED so overwriting the map
visibly changes protected bytes rather than writing identical bytes.

### F3. Execute the skill's refresh-and-query procedure

OBSERVED code: `.agents/skills/cwng-impact-map/SKILL.md:35` now describes refresh,
its separate output directory, currency/error interpretation, CI downloads, and
querying the generated map. It keeps committed regeneration as a maintainer task.

OBSERVED test: `tests/unit/test_impact_map.py:421` extracts the documented shell
recipe, executes it with the repo virtualenv against local synthetic history,
and queries a new symbol absent from the committed snapshot. It requires stale
currency with the current tree, a successful query through the fresh map, and
unchanged committed bytes. Before the skill change it was RED because no recipe
existed; afterward the complete procedure is GREEN. This executes the commands
and checks their effects; the presence of a source string alone cannot pass it.

### All new tests: RED, then GREEN

OBSERVED command before any fixes:
`python3 -m pytest tests/unit/test_impact_map.py tests/unit/test_summary_gate_requires_success.py -p no:randomly -k 'current_path_mismatches or output_aliases or repository_files or rechecks_aliases or does_not_follow_links or skill_refresh or gate_rejects_collapse or never_authorizes_auto_revert' --tb=short`:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 82 items / 66 deselected / 16 selected

tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package] FAILED [  6%]
tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules] FAILED [ 12%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink] FAILED [ 18%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink] FAILED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink] FAILED [ 31%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink] FAILED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py] FAILED [ 43%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[tests/unit/test_impact_map.py] FAILED [ 50%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[map] FAILED [ 56%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[summary] FAILED [ 62%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[symlink] FAILED [ 68%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[hardlink] FAILED [ 75%]
tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map FAILED [ 81%]
tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement FAILED [ 87%]
tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[False] FAILED [ 93%]
tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[True] FAILED [100%]

=================================== FAILURES ===================================
__ test_refresh_reports_current_path_mismatches_as_misses[module-to-package] ___
tests/unit/test_impact_map.py:329: in test_refresh_reports_current_path_mismatches_as_misses
    assert result.returncode == 0, result.stderr
E   AssertionError: Traceback (most recent call last):
E       File "$ROOT/scripts/impact_map.py", line 1413, in <module>
E         raise SystemExit(main())
E                          ^^^^^^
E       File "$ROOT/scripts/impact_map.py", line 1365, in main
E         refresh_artifacts(
E       File "$ROOT/scripts/impact_map.py", line 1291, in refresh_artifacts
E         raise ValueError("recall evidence unavailable: fetch full history and check declared evidence paths")
E     ValueError: recall evidence unavailable: fetch full history and check declared evidence paths
E
E   assert 1 == 0
E    +  where 1 = CompletedProcess(args=['$VENV/bin/python', '$ROOT/scripts/impact_map.py', '--repo-root', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_reports_current_p0/repo', 'refresh', '--output-dir', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_reports_current_p0/artifacts', '--summary', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_reports_current_p0/summary.md'], returncode=1, stdout='', stderr='Traceback (most recent call last):\n  File "$ROOT/scripts/impact_map.py", line 1413, in <module>\n    raise SystemExit(main())\n                     ^^^^^^\n  File "$ROOT/scripts/impact_map.py", line 1365, in main\n    refresh_artifacts(\n  File "$ROOT/scripts/impact_map.py", line 1291, in refresh_artifacts\n    raise ValueError("recall evidence unavailable: fetch full history and check declared evidence paths")\nValueError: recall evidence unavailable: fetch full history and check declared evidence paths\n').returncode
____ test_refresh_reports_current_path_mismatches_as_misses[remove-modules] ____
tests/unit/test_impact_map.py:329: in test_refresh_reports_current_path_mismatches_as_misses
    assert result.returncode == 0, result.stderr
E   AssertionError: Traceback (most recent call last):
E       File "$ROOT/scripts/impact_map.py", line 1413, in <module>
E         raise SystemExit(main())
E                          ^^^^^^
E       File "$ROOT/scripts/impact_map.py", line 1365, in main
E         refresh_artifacts(
E       File "$ROOT/scripts/impact_map.py", line 1291, in refresh_artifacts
E         raise ValueError("recall evidence unavailable: fetch full history and check declared evidence paths")
E     ValueError: recall evidence unavailable: fetch full history and check declared evidence paths
E
E   assert 1 == 0
E    +  where 1 = CompletedProcess(args=['$VENV/bin/python', '$ROOT/scripts/impact_map.py', '--repo-root', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_reports_current_p1/repo', 'refresh', '--output-dir', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_reports_current_p1/artifacts', '--summary', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_reports_current_p1/summary.md'], returncode=1, stdout='', stderr='Traceback (most recent call last):\n  File "$ROOT/scripts/impact_map.py", line 1413, in <module>\n    raise SystemExit(main())\n                     ^^^^^^\n  File "$ROOT/scripts/impact_map.py", line 1365, in main\n    refresh_artifacts(\n  File "$ROOT/scripts/impact_map.py", line 1291, in refresh_artifacts\n    raise ValueError("recall evidence unavailable: fetch full history and check declared evidence paths")\nValueError: recall evidence unavailable: fetch full history and check declared evidence paths\n').returncode
_ test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink] _
tests/unit/test_impact_map.py:358: in test_refresh_rejects_output_aliases_before_writes
    assert result.returncode != 0, "an output alias was accepted"
E   AssertionError: an output alias was accepted
E   assert 0 != 0
E    +  where 0 = CompletedProcess(args=['$VENV/bin/python', '$ROOT/scripts/impact_map.py', '--repo-root', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al0/repo', 'refresh', '--output-dir', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al0/artifacts', '--summary', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al0/summary.md'], returncode=0, stdout='## Impact map currency\n\nCommitted artifacts: **current**. Fresh artifacts are attached to this CI run.\nStaleness is advisory; contributors do not need to regenerate or commit these files.\n\nChecked commit: `a84c1e20f1b011ca4efa7982a9d2e4e009c30812`\nCurrent cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`\nCommitted map cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`\n\n- `impact-map.json`: current\n- `impact-map-recall.json`: current\n\nCurated recall: **1/2 (50.00%)**; misses=1. This constructed case set is not an independent measurement.\n- Miss `a84c1e20f1b011ca4efa7982a9d2e4e009c30812`: `cps.app:dynamic` → `cps.provider:Worker`: no_static_call_path\n', stderr='').returncode
_ test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink] _
tests/unit/test_impact_map.py:357: in test_refresh_rejects_output_aliases_before_writes
    assert unchanged, "generated output overwrote a protected input"
E   AssertionError: generated output overwrote a protected input
E   assert False
_ test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink] _
tests/unit/test_impact_map.py:358: in test_refresh_rejects_output_aliases_before_writes
    assert result.returncode != 0, "an output alias was accepted"
E   AssertionError: an output alias was accepted
E   assert 0 != 0
E    +  where 0 = CompletedProcess(args=['$VENV/bin/python', '$ROOT/scripts/impact_map.py', '--repo-root', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al2/repo', 'refresh', '--output-dir', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al2/artifacts', '--summary', '$TMPDIR/cwng-pytest/1024/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al2/summary.md'], returncode=0, stdout='## Impact map currency\n\nCommitted artifacts: **current**. Fresh artifacts are attached to this CI run.\nStaleness is advisory; contributors do not need to regenerate or commit these files.\n\nChecked commit: `5f3e4b2f1af51478c45d91e9a19faeccc4d7d5f9`\nCurrent cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`\nCommitted map cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`\n\n- `impact-map.json`: current\n- `impact-map-recall.json`: current\n\nCurated recall: **1/2 (50.00%)**; misses=1. This constructed case set is not an independent measurement.\n- Miss `5f3e4b2f1af51478c45d91e9a19faeccc4d7d5f9`: `cps.app:dynamic` → `cps.provider:Worker`: no_static_call_path\n', stderr='').returncode
_ test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink] _
tests/unit/test_impact_map.py:357: in test_refresh_rejects_output_aliases_before_writes
    assert unchanged, "generated output overwrote a protected input"
E   AssertionError: generated output overwrote a protected input
E   assert False
_ test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py] _
tests/unit/test_impact_map.py:380: in test_refresh_rejects_summary_aliases_to_repository_files
    assert unchanged, "summary appended to a repository source file"
E   AssertionError: summary appended to a repository source file
E   assert False
_ test_refresh_rejects_summary_aliases_to_repository_files[tests/unit/test_impact_map.py] _
tests/unit/test_impact_map.py:380: in test_refresh_rejects_summary_aliases_to_repository_files
    assert unchanged, "summary appended to a repository source file"
E   AssertionError: summary appended to a repository source file
E   assert False
_______________ test_refresh_rechecks_aliases_at_write_time[map] _______________
tests/unit/test_impact_map.py:402: in test_refresh_rechecks_aliases_at_write_time
    with pytest.raises(ValueError, match="destination aliases"):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   Failed: DID NOT RAISE <class 'ValueError'>
----------------------------- Captured stdout call -----------------------------
## Impact map currency

Committed artifacts: **current**. Fresh artifacts are attached to this CI run.
Staleness is advisory; contributors do not need to regenerate or commit these files.

Checked commit: `9a53dced22f90a654f89651b1d744feeff39b742`
Current cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`
Committed map cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`

- `impact-map.json`: current
- `impact-map-recall.json`: current

Curated recall: **1/2 (50.00%)**; misses=1. This constructed case set is not an independent measurement.
- Miss `9a53dced22f90a654f89651b1d744feeff39b742`: `cps.app:dynamic` → `cps.provider:Worker`: no_static_call_path
_____________ test_refresh_rechecks_aliases_at_write_time[summary] _____________
tests/unit/test_impact_map.py:402: in test_refresh_rechecks_aliases_at_write_time
    with pytest.raises(ValueError, match="destination aliases"):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   Failed: DID NOT RAISE <class 'ValueError'>
----------------------------- Captured stdout call -----------------------------
## Impact map currency

Committed artifacts: **current**. Fresh artifacts are attached to this CI run.
Staleness is advisory; contributors do not need to regenerate or commit these files.

Checked commit: `6df6e2c7cfd51d16d145fc2af3c08d07d9b7bc01`
Current cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`
Committed map cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`

- `impact-map.json`: current
- `impact-map-recall.json`: current

Curated recall: **1/2 (50.00%)**; misses=1. This constructed case set is not an independent measurement.
- Miss `6df6e2c7cfd51d16d145fc2af3c08d07d9b7bc01`: `cps.app:dynamic` → `cps.provider:Worker`: no_static_call_path
________________ test_write_json_does_not_follow_links[symlink] ________________
tests/unit/test_impact_map.py:415: in test_write_json_does_not_follow_links
    assert target.read_text(encoding="utf-8") == '{"input": true}\n'
E   assert '{\n  "output": true\n}\n' == '{"input": true}\n'
E
E     - {"input": true}
E     + {
E     +   "output": true
E     + }
_______________ test_write_json_does_not_follow_links[hardlink] ________________
tests/unit/test_impact_map.py:415: in test_write_json_does_not_follow_links
    assert target.read_text(encoding="utf-8") == '{"input": true}\n'
E   assert '{\n  "output": true\n}\n' == '{"input": true}\n'
E
E     - {"input": true}
E     + {
E     +   "output": true
E     + }
__________ test_skill_refresh_recipe_publishes_and_queries_fresh_map ___________
tests/unit/test_impact_map.py:425: in test_skill_refresh_recipe_publishes_and_queries_fresh_map
    assert recipes, "skill provides no executable refresh recipe"
E   AssertionError: skill provides no executable refresh recipe
E   assert []
_____ test_committed_recall_gate_rejects_collapse_and_accepts_improvement ______
tests/unit/test_impact_map.py:462: in test_committed_recall_gate_rejects_collapse_and_accepts_improvement
    with pytest.raises(AssertionError, match="committed recall fell below"):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   Failed: DID NOT RAISE <class 'AssertionError'>
_________ test_impact_map_failure_never_authorizes_auto_revert[False] __________
tests/unit/test_summary_gate_requires_success.py:124: in test_impact_map_failure_never_authorizes_auto_revert
    assert decision == "revert=false", out
E   AssertionError: failed jobs: Impact Map (regeneration + currency)
E     revert-worthy failure(s): Impact Map (regeneration + currency)
E
E   assert 'revert=true' == 'revert=false'
E
E     - revert=false
E     + revert=true
__________ test_impact_map_failure_never_authorizes_auto_revert[True] __________
tests/unit/test_summary_gate_requires_success.py:124: in test_impact_map_failure_never_authorizes_auto_revert
    assert decision == "revert=false", out
E   AssertionError: failed jobs: Impact Map (regeneration + currency)|Test Suite Summary
E     revert-worthy failure(s): Impact Map (regeneration + currency)
E
E   assert 'revert=true' == 'revert=false'
E
E     - revert=false
E     + revert=true
============================= slowest 10 durations =============================
14.30s setup    tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules]
12.78s setup    tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package]
10.47s call     tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package]
7.97s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink]
7.72s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py]
6.10s call     tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement
5.18s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink]
5.13s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink]
4.13s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink]
3.98s call     tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules]
=========================== short test summary info ============================
FAILED tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package]
FAILED tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[tests/unit/test_impact_map.py]
FAILED tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[map]
FAILED tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[summary]
FAILED tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[symlink]
FAILED tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[hardlink]
FAILED tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map
FAILED tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement
FAILED tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[False]
FAILED tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[True]
================ 16 failed, 66 deselected in 110.48s (0:01:50) =================

exit code: 1
```

OBSERVED strengthened output-alias RED, still before the writer fix:
`python3 -m pytest tests/unit/test_impact_map.py -p no:randomly -k output_aliases --tb=short`:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 41 items / 37 deselected / 4 selected

tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink] FAILED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink] FAILED [ 50%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink] FAILED [ 75%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink] FAILED [100%]

=================================== FAILURES ===================================
_ test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink] _
tests/unit/test_impact_map.py:358: in test_refresh_rejects_output_aliases_before_writes
    assert unchanged, "generated output overwrote a protected input"
E   AssertionError: generated output overwrote a protected input
E   assert False
_ test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink] _
tests/unit/test_impact_map.py:358: in test_refresh_rejects_output_aliases_before_writes
    assert unchanged, "generated output overwrote a protected input"
E   AssertionError: generated output overwrote a protected input
E   assert False
_ test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink] _
tests/unit/test_impact_map.py:359: in test_refresh_rejects_output_aliases_before_writes
    assert result.returncode != 0, "an output alias was accepted"
E   AssertionError: an output alias was accepted
E   assert 0 != 0
E    +  where 0 = CompletedProcess(args=['$VENV/bin/python', '$ROOT/scripts/impact_map.py', '--repo-root', '$TMPDIR/cwng-pytest/14869/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al2/repo', 'refresh', '--output-dir', '$TMPDIR/cwng-pytest/14869/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al2/artifacts', '--summary', '$TMPDIR/cwng-pytest/14869/pytest-of-fixture-user/pytest-0/test_refresh_rejects_output_al2/summary.md'], returncode=0, stdout='## Impact map currency\n\nCommitted artifacts: **stale**. Fresh artifacts are attached to this CI run.\nStaleness is advisory; contributors do not need to regenerate or commit these files.\n\nChecked commit: `f21b3441e88d147716bbd37ed45a31954f5365b8`\nCurrent cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`\nCommitted map cps tree: `ed6985b391228938c8d434a8792e08f5024cd0bf`\n\n- `impact-map.json`: differs or missing\n- `impact-map-recall.json`: current\n\nCurated recall: **1/2 (50.00%)**; misses=1. This constructed case set is not an independent measurement.\n- Miss `f21b3441e88d147716bbd37ed45a31954f5365b8`: `cps.app:dynamic` → `cps.provider:Worker`: no_static_call_path\n', stderr='').returncode
_ test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink] _
tests/unit/test_impact_map.py:358: in test_refresh_rejects_output_aliases_before_writes
    assert unchanged, "generated output overwrote a protected input"
E   AssertionError: generated output overwrote a protected input
E   assert False
============================= slowest 10 durations =============================
3.51s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink]
3.20s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink]
2.75s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink]
2.66s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink]
1.10s call     tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink]
0.90s call     tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink]
0.85s call     tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink]
0.75s call     tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink]

(2 durations < 0.005s hidden.  Use -vv to show these durations.)
=========================== short test summary info ============================
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink]
FAILED tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink]
====================== 4 failed, 37 deselected in 16.44s =======================

exit code: 1
```

OBSERVED GREEN for all sixteen new cases, using the first command above:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 82 items / 66 deselected / 16 selected

tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package] PASSED [  6%]
tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules] PASSED [ 12%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink] PASSED [ 18%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink] PASSED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink] PASSED [ 31%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink] PASSED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py] PASSED [ 43%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[tests/unit/test_impact_map.py] PASSED [ 50%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[map] PASSED [ 56%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[summary] PASSED [ 62%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[symlink] PASSED [ 68%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[hardlink] PASSED [ 75%]
tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map PASSED [ 81%]
tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement PASSED [ 87%]
tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[False] PASSED [ 93%]
tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[True] PASSED [100%]

============================= slowest 10 durations =============================
8.31s call     tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement
3.26s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink]
3.25s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink]
3.01s setup    tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map
2.86s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink]
2.68s setup    tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package]
2.52s setup    tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules]
2.52s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py]
2.49s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[tests/unit/test_impact_map.py]
2.46s setup    tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[map]
====================== 16 passed, 66 deselected in 49.07s ======================

exit code: 0
```

### Full-tree baseline and fixed reproductions

OBSERVED initial baseline reproduction of both B1 cases and a call-edge collapse:

```text
$ git sparse-checkout set cps scripts state tests .agents docs
exit code: 0
$ git checkout --quiet
exit code: 0
$ git remote set-url origin $ORIGIN
exit code: 0
$ git reset --hard 4a5f972752d8fa595def4f783f5ed7b9e1d349ef
HEAD is now at 4a5f972752 docs(impact-map): record consecutive full passes and final validation
exit code: 0
B1 baseline: relocation
$ git mv cps/web.py cps/web/__init__.py
exit code: 0
$ git -c user.name=new-usemame -c user.email=248195428+new-usemame@users.noreply.github.com commit -m Exercise relocation against frozen recall evidence
[mod/p0.2b-impact-map-ci 018baa47e3] Exercise relocation against frozen recall evidence
 1 file changed, 0 insertions(+), 0 deletions(-)
 rename cps/{web.py => web/__init__.py} (100%)
exit code: 0
$ $VENV/bin/python scripts/impact_map.py refresh --output-dir $ROOT/tmp/p0.2b/hold/real-tree/tmp/hold-baseline-relocation
Traceback (most recent call last):
  File "$ROOT/tmp/p0.2b/hold/real-tree/scripts/impact_map.py", line 1413, in <module>
    raise SystemExit(main())
                     ^^^^^^
  File "$ROOT/tmp/p0.2b/hold/real-tree/scripts/impact_map.py", line 1365, in main
    refresh_artifacts(
  File "$ROOT/tmp/p0.2b/hold/real-tree/scripts/impact_map.py", line 1291, in refresh_artifacts
    raise ValueError("recall evidence unavailable: fetch full history and check declared evidence paths")
ValueError: recall evidence unavailable: fetch full history and check declared evidence paths
exit code: 1
{
  "hits": 7,
  "total": 10,
  "misses": [
    {
      "commit": "d1628a3a94745ef97fc2f9c87a9b30f1fc7744fa",
      "affected_site": "cps.web:render_magic_shelf",
      "changed_symbol": "cps.custom_column_sort:resolve_magic_shelf_sort",
      "evidence_paths_present": false,
      "miss_reason": "historical_diff_does_not_touch_declared_sites"
    },
    {
      "commit": "430601d6a58012dc5e8017431feed25f1b0fe38c",
      "affected_site": "frontend/src/pages/Shelf.tsx",
      "changed_symbol": "cps.api.shelves:shelf_detail",
      "evidence_paths_present": true,
      "miss_reason": "affected_site_not_present_in_current_map"
    },
    {
      "commit": "9dc72ed57e328855b9d19653d831eeee7abea08b",
      "affected_site": "cps.api.shelves:shelf_detail",
      "changed_symbol": "cps.db:public_shelf_book_filter",
      "evidence_paths_present": true,
      "miss_reason": "The dependency crosses an instance-method call and keyword-controlled branch; methods are folded into a class node and the receiver call is unresolved."
    }
  ]
}
$ git reset --hard 4a5f972752d8fa595def4f783f5ed7b9e1d349ef
HEAD is now at 4a5f972752 docs(impact-map): record consecutive full passes and final validation
exit code: 0
B1 baseline: removal
$ git rm cps/cwa_functions.py cps/schedule.py
rm 'cps/cwa_functions.py'
rm 'cps/schedule.py'
exit code: 0
$ git -c user.name=new-usemame -c user.email=248195428+new-usemame@users.noreply.github.com commit -m Exercise removal against frozen recall evidence
[mod/p0.2b-impact-map-ci 611999a4c8] Exercise removal against frozen recall evidence
 2 files changed, 3321 deletions(-)
 delete mode 100755 cps/cwa_functions.py
 delete mode 100755 cps/schedule.py
exit code: 0
$ $VENV/bin/python scripts/impact_map.py refresh --output-dir $ROOT/tmp/p0.2b/hold/real-tree/tmp/hold-baseline-removal
Traceback (most recent call last):
  File "$ROOT/tmp/p0.2b/hold/real-tree/scripts/impact_map.py", line 1413, in <module>
    raise SystemExit(main())
                     ^^^^^^
  File "$ROOT/tmp/p0.2b/hold/real-tree/scripts/impact_map.py", line 1365, in main
    refresh_artifacts(
  File "$ROOT/tmp/p0.2b/hold/real-tree/scripts/impact_map.py", line 1291, in refresh_artifacts
    raise ValueError("recall evidence unavailable: fetch full history and check declared evidence paths")
ValueError: recall evidence unavailable: fetch full history and check declared evidence paths
exit code: 1
{
  "hits": 7,
  "total": 10,
  "misses": [
    {
      "commit": "d8ca3fbd84af77fb931f884e6dfef1992e240df1",
      "affected_site": "cps.cwa_functions:set_cwa_settings",
      "changed_symbol": "cps.schedule:resolve_hardcover_auto_fetch_schedule",
      "evidence_paths_present": false,
      "miss_reason": "historical_diff_does_not_touch_declared_sites"
    },
    {
      "commit": "430601d6a58012dc5e8017431feed25f1b0fe38c",
      "affected_site": "frontend/src/pages/Shelf.tsx",
      "changed_symbol": "cps.api.shelves:shelf_detail",
      "evidence_paths_present": true,
      "miss_reason": "affected_site_not_present_in_current_map"
    },
    {
      "commit": "9dc72ed57e328855b9d19653d831eeee7abea08b",
      "affected_site": "cps.api.shelves:shelf_detail",
      "changed_symbol": "cps.db:public_shelf_book_filter",
      "evidence_paths_present": true,
      "miss_reason": "The dependency crosses an instance-method call and keyword-controlled branch; methods are folded into a class node and the receiver call is unresolved."
    }
  ]
}
$ git reset --hard 4a5f972752d8fa595def4f783f5ed7b9e1d349ef
HEAD is now at 4a5f972752 docs(impact-map): record consecutive full passes and final validation
exit code: 0
B3 baseline: collapsed map with regenerated matching recall
edges: 9475 -> 1875
$ $VENV/bin/python scripts/impact_map.py recall
historical recall: 0/10 (0.00%); misses=10
exit code: 0
$ $VENV/bin/python -m pytest tests/unit/test_impact_map.py -p no:randomly --tb=short
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT/tmp/p0.2b/hold/real-tree
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 27 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  3%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [  7%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [ 11%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [ 14%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [ 18%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [ 22%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [ 29%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 33%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] PASSED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] PASSED [ 40%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] PASSED [ 44%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] PASSED [ 48%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] PASSED [ 51%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] PASSED [ 55%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] PASSED [ 59%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] PASSED [ 62%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] PASSED [ 66%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] PASSED [ 70%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] PASSED [ 74%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] PASSED [ 77%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] PASSED [ 81%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] PASSED [ 85%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] PASSED [ 88%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] PASSED [ 92%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses PASSED [ 96%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [100%]

============================= slowest 10 durations =============================
7.88s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
5.92s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
4.55s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent]
4.45s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink]
4.20s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct]
4.14s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct]
4.12s setup    tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
4.00s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct]
3.92s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink]
3.87s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct]
======================== 27 passed in 114.02s (0:01:54) ========================
exit code: 0

exit code: 0
```

The initial collapse retained route-handler edges (1,875 remaining). To match
the judge's count exactly, the next run retained only imports (1,355 remaining):

```text
$ git reset --hard 4a5f972752d8fa595def4f783f5ed7b9e1d349ef
HEAD is now at 4a5f972752 docs(impact-map): record consecutive full passes and final validation
exit code: 0
B3 baseline: collapsed map with regenerated matching recall
edges: 9475 -> 1355
$ $VENV/bin/python scripts/impact_map.py recall
historical recall: 0/10 (0.00%); misses=10
exit code: 0
$ $VENV/bin/python -m pytest tests/unit/test_impact_map.py -p no:randomly --tb=short
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT/tmp/p0.2b/hold/real-tree
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 27 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  3%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [  7%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [ 11%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [ 14%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [ 18%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [ 22%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [ 25%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [ 29%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 33%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] PASSED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] PASSED [ 40%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] PASSED [ 44%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] PASSED [ 48%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] PASSED [ 51%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] PASSED [ 55%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] PASSED [ 59%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] PASSED [ 62%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] PASSED [ 66%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] PASSED [ 70%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] PASSED [ 74%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] PASSED [ 77%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] PASSED [ 81%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] PASSED [ 85%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] PASSED [ 88%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] PASSED [ 92%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses PASSED [ 96%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [100%]

============================= slowest 10 durations =============================
5.88s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
3.73s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
3.45s setup    tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
3.34s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct]
3.30s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases]
3.16s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
2.97s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct]
2.92s call     tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses
2.69s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct]
2.61s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink]
======================== 27 passed in 77.79s (0:01:17) =========================
exit code: 0

exit code: 0
```

OBSERVED fixed phase: both B1 cases exit zero; the exact B3 collapse makes the
complete impact-map test file RED. The wrapper exits zero because it explicitly
requires those expected child exit codes; the suite's own exit code is 1.

```text
$ git reset --hard 4a5f972752d8fa595def4f783f5ed7b9e1d349ef
HEAD is now at 4a5f972752 docs(impact-map): record consecutive full passes and final validation
exit code: 0
B1 fixed: relocation
$ git mv cps/web.py cps/web/__init__.py
exit code: 0
$ git -c user.name=new-usemame -c user.email=248195428+new-usemame@users.noreply.github.com commit -m Exercise relocation against frozen recall evidence
[mod/p0.2b-impact-map-ci 19d0ae65bb] Exercise relocation against frozen recall evidence
 1 file changed, 0 insertions(+), 0 deletions(-)
 rename cps/{web.py => web/__init__.py} (100%)
exit code: 0
$ $VENV/bin/python scripts/impact_map.py refresh --output-dir $ROOT/tmp/p0.2b/hold/real-tree/tmp/hold-fixed-relocation
## Impact map currency

Committed artifacts: **stale**. Fresh artifacts are attached to this CI run.
Staleness is advisory; contributors do not need to regenerate or commit these files.

Checked commit: `19d0ae65bb6efb8136b0f78b7a4f2d11e494cf8e`
Current cps tree: `5d29199110c7ba283b4b156fb37b00aa14dda6e0`
Committed map cps tree: `769be5deb55520e94165317132e91af3b38794c6`

- `impact-map.json`: differs or missing
- `impact-map-recall.json`: differs or missing

Curated recall: **7/10 (70.00%)**; misses=3. This constructed case set is not an independent measurement.
- Miss `d1628a3a94745ef97fc2f9c87a9b30f1fc7744fa`: `cps.web:render_magic_shelf` → `cps.custom_column_sort:resolve_magic_shelf_sort`: historical_diff_does_not_touch_declared_sites
- Miss `430601d6a58012dc5e8017431feed25f1b0fe38c`: `frontend/src/pages/Shelf.tsx` → `cps.api.shelves:shelf_detail`: affected_site_not_present_in_current_map
- Miss `9dc72ed57e328855b9d19653d831eeee7abea08b`: `cps.api.shelves:shelf_detail` → `cps.db:public_shelf_book_filter`: The dependency crosses an instance-method call and keyword-controlled branch; methods are folded into a class node and the receiver call is unresolved.
exit code: 0
{
  "hits": 7,
  "total": 10,
  "misses": [
    {
      "commit": "d1628a3a94745ef97fc2f9c87a9b30f1fc7744fa",
      "affected_site": "cps.web:render_magic_shelf",
      "changed_symbol": "cps.custom_column_sort:resolve_magic_shelf_sort",
      "evidence_paths_present": false,
      "miss_reason": "historical_diff_does_not_touch_declared_sites"
    },
    {
      "commit": "430601d6a58012dc5e8017431feed25f1b0fe38c",
      "affected_site": "frontend/src/pages/Shelf.tsx",
      "changed_symbol": "cps.api.shelves:shelf_detail",
      "evidence_paths_present": true,
      "miss_reason": "affected_site_not_present_in_current_map"
    },
    {
      "commit": "9dc72ed57e328855b9d19653d831eeee7abea08b",
      "affected_site": "cps.api.shelves:shelf_detail",
      "changed_symbol": "cps.db:public_shelf_book_filter",
      "evidence_paths_present": true,
      "miss_reason": "The dependency crosses an instance-method call and keyword-controlled branch; methods are folded into a class node and the receiver call is unresolved."
    }
  ]
}
$ git reset --hard 4a5f972752d8fa595def4f783f5ed7b9e1d349ef
HEAD is now at 4a5f972752 docs(impact-map): record consecutive full passes and final validation
exit code: 0
B1 fixed: removal
$ git rm cps/cwa_functions.py cps/schedule.py
rm 'cps/cwa_functions.py'
rm 'cps/schedule.py'
exit code: 0
$ git -c user.name=new-usemame -c user.email=248195428+new-usemame@users.noreply.github.com commit -m Exercise removal against frozen recall evidence
[mod/p0.2b-impact-map-ci 9cafb242fe] Exercise removal against frozen recall evidence
 2 files changed, 3321 deletions(-)
 delete mode 100755 cps/cwa_functions.py
 delete mode 100755 cps/schedule.py
exit code: 0
$ $VENV/bin/python scripts/impact_map.py refresh --output-dir $ROOT/tmp/p0.2b/hold/real-tree/tmp/hold-fixed-removal
## Impact map currency

Committed artifacts: **stale**. Fresh artifacts are attached to this CI run.
Staleness is advisory; contributors do not need to regenerate or commit these files.

Checked commit: `9cafb242fe25266d20b6b6d2f7017d29b2299cfc`
Current cps tree: `8dffaef8d1daff1a7383bcffe031fe4fe39180ba`
Committed map cps tree: `769be5deb55520e94165317132e91af3b38794c6`

- `impact-map.json`: differs or missing
- `impact-map-recall.json`: differs or missing

Curated recall: **7/10 (70.00%)**; misses=3. This constructed case set is not an independent measurement.
- Miss `d8ca3fbd84af77fb931f884e6dfef1992e240df1`: `cps.cwa_functions:set_cwa_settings` → `cps.schedule:resolve_hardcover_auto_fetch_schedule`: historical_diff_does_not_touch_declared_sites
- Miss `430601d6a58012dc5e8017431feed25f1b0fe38c`: `frontend/src/pages/Shelf.tsx` → `cps.api.shelves:shelf_detail`: affected_site_not_present_in_current_map
- Miss `9dc72ed57e328855b9d19653d831eeee7abea08b`: `cps.api.shelves:shelf_detail` → `cps.db:public_shelf_book_filter`: The dependency crosses an instance-method call and keyword-controlled branch; methods are folded into a class node and the receiver call is unresolved.
exit code: 0
{
  "hits": 7,
  "total": 10,
  "misses": [
    {
      "commit": "d8ca3fbd84af77fb931f884e6dfef1992e240df1",
      "affected_site": "cps.cwa_functions:set_cwa_settings",
      "changed_symbol": "cps.schedule:resolve_hardcover_auto_fetch_schedule",
      "evidence_paths_present": false,
      "miss_reason": "historical_diff_does_not_touch_declared_sites"
    },
    {
      "commit": "430601d6a58012dc5e8017431feed25f1b0fe38c",
      "affected_site": "frontend/src/pages/Shelf.tsx",
      "changed_symbol": "cps.api.shelves:shelf_detail",
      "evidence_paths_present": true,
      "miss_reason": "affected_site_not_present_in_current_map"
    },
    {
      "commit": "9dc72ed57e328855b9d19653d831eeee7abea08b",
      "affected_site": "cps.api.shelves:shelf_detail",
      "changed_symbol": "cps.db:public_shelf_book_filter",
      "evidence_paths_present": true,
      "miss_reason": "The dependency crosses an instance-method call and keyword-controlled branch; methods are folded into a class node and the receiver call is unresolved."
    }
  ]
}
$ git reset --hard 4a5f972752d8fa595def4f783f5ed7b9e1d349ef
HEAD is now at 4a5f972752 docs(impact-map): record consecutive full passes and final validation
exit code: 0
B3 fixed: collapsed map with regenerated matching recall
edges: 9475 -> 1355
$ $VENV/bin/python scripts/impact_map.py recall
historical recall: 0/10 (0.00%); misses=10
exit code: 0
$ $VENV/bin/python -m pytest tests/unit/test_impact_map.py -p no:randomly --tb=short
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT/tmp/p0.2b/hold/real-tree
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 41 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  2%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [  4%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [  7%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [  9%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [ 12%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [ 14%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [ 17%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [ 19%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 21%]
tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package] PASSED [ 24%]
tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules] PASSED [ 26%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink] PASSED [ 29%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink] PASSED [ 31%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink] PASSED [ 34%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink] PASSED [ 36%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py] PASSED [ 39%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[tests/unit/test_impact_map.py] PASSED [ 41%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[map] PASSED [ 43%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[summary] PASSED [ 46%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[symlink] PASSED [ 48%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[hardlink] PASSED [ 51%]
tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map PASSED [ 53%]
tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement FAILED [ 56%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] PASSED [ 58%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] PASSED [ 60%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] PASSED [ 63%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] PASSED [ 65%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] PASSED [ 68%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] PASSED [ 70%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] PASSED [ 73%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] PASSED [ 75%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] PASSED [ 78%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] PASSED [ 80%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] PASSED [ 82%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] PASSED [ 85%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] PASSED [ 87%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] PASSED [ 90%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] PASSED [ 92%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] PASSED [ 95%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses FAILED [ 97%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [100%]

=================================== FAILURES ===================================
_____ test_committed_recall_gate_rejects_collapse_and_accepts_improvement ______
tests/unit/test_impact_map.py:477: in test_committed_recall_gate_rejects_collapse_and_accepts_improvement
    assert report["hits"] >= 9
E   assert 1 >= 9
________ test_committed_recall_report_is_reproducible_and_keeps_misses _________
tests/unit/test_impact_map.py:576: in test_committed_recall_report_is_reproducible_and_keeps_misses
    assert observed["hits"] >= 8, "committed recall fell below the eight-case floor"
E   AssertionError: committed recall fell below the eight-case floor
E   assert 0 >= 8
============================= slowest 10 durations =============================
8.75s call     tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement
8.10s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
3.34s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
2.76s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases]
2.71s setup    tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map
2.64s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink]
2.62s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink]
2.57s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
2.54s setup    tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink]
2.41s setup    tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
=========================== short test summary info ============================
FAILED tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement
FAILED tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses
=================== 2 failed, 39 passed in 113.78s (0:01:53) ===================
exit code: 1

exit code: 0
```

Reproduction mechanics (executed in the disposable clone):

```bash
git mv cps/web.py cps/web/__init__.py
# Commit the move, then run refresh with a separate output directory.
# Reset only the disposable clone before the independent removal scenario.
git rm cps/cwa_functions.py cps/schedule.py
# Commit the removal, then run refresh again.
```

The collapse keeps `edge["kind"] == "import"`, writes the modified map, runs
`python3 scripts/impact_map.py recall`, then runs the complete unit file. It does
not alter the map's declared counts or tune cases to manufacture a hit rate.


### Final regeneration and consecutive full runs of both changed files

OBSERVED: `python3 scripts/impact_map.py build`, then
`python3 scripts/impact_map.py recall`, on the supplied worktree:

```text
wrote $ROOT/state/modernization/impact-map.json: 3396 nodes, 9475 edges, 15299 blind spots

exit code: 0
```

```text
historical recall: 8/10 (80.00%); misses=2

exit code: 0
```

OBSERVED: neither committed artifact changed. The current snapshot remains
8/10 at cps tree `769be5deb55520e94165317132e91af3b38794c6`; the deliberately
refactored and corrupted artifacts above exist only in the disposable clone.

OBSERVED: two consecutive complete invocations of this command, with source
and test code unchanged between them:

```bash
python3 -m pytest tests/unit/test_impact_map.py tests/unit/test_summary_gate_requires_success.py -p no:randomly
```

Each run includes all 41 impact-map cases and all 41 summary/revert cases:
82 total, no skips or deselections. First complete run:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 82 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  1%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [  2%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [  3%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [  4%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [  6%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [  7%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [  8%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [  9%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 10%]
tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package] PASSED [ 12%]
tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules] PASSED [ 13%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink] PASSED [ 14%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink] PASSED [ 15%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink] PASSED [ 17%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink] PASSED [ 18%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py] PASSED [ 19%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[tests/unit/test_impact_map.py] PASSED [ 20%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[map] PASSED [ 21%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[summary] PASSED [ 23%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[symlink] PASSED [ 24%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[hardlink] PASSED [ 25%]
tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map PASSED [ 26%]
tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement PASSED [ 28%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] PASSED [ 29%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] PASSED [ 30%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] PASSED [ 31%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] PASSED [ 32%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] PASSED [ 34%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] PASSED [ 35%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] PASSED [ 36%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] PASSED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] PASSED [ 39%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] PASSED [ 40%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] PASSED [ 41%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] PASSED [ 42%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] PASSED [ 43%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] PASSED [ 45%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] PASSED [ 46%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] PASSED [ 47%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses PASSED [ 48%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [ 50%]
tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[False] PASSED [ 51%]
tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[True] PASSED [ 52%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge] PASSED [ 53%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/main] PASSED [ 54%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/dev] PASSED [ 56%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/tags/v1.0.0] PASSED [ 57%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-workflow_dispatch-refs/heads/main] PASSED [ 58%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-pull_request-refs/pull/1/merge] PASSED [ 59%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/main] PASSED [ 60%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/dev] PASSED [ 62%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/tags/v1.0.0] PASSED [ 63%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-workflow_dispatch-refs/heads/main] PASSED [ 64%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-pull_request-refs/pull/1/merge] PASSED [ 65%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/main] PASSED [ 67%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/dev] PASSED [ 68%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/tags/v1.0.0] PASSED [ 69%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-workflow_dispatch-refs/heads/main] PASSED [ 70%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-pull_request-refs/pull/1/merge] PASSED [ 71%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/main] PASSED [ 73%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/dev] PASSED [ 74%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/tags/v1.0.0] PASSED [ 75%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-workflow_dispatch-refs/heads/main] PASSED [ 76%]
tests/unit/test_summary_gate_requires_success.py::test_positive_control_all_success_passes PASSED [ 78%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[failure] PASSED [ 79%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[skipped] PASSED [ 80%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[cancelled] PASSED [ 81%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_the_other_hard_gated_lanes_too[fast-tests-kwargs0] PASSED [ 82%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_the_other_hard_gated_lanes_too[frontend-build-kwargs1] PASSED [ 84%]
tests/unit/test_summary_gate_requires_success.py::test_integration_stays_advisory_on_main PASSED [ 85%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[failure] PASSED [ 86%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[skipped] PASSED [ 87%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[cancelled] PASSED [ 89%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[failure] PASSED [ 90%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[skipped] PASSED [ 91%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[cancelled] PASSED [ 92%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_changed_paths_success[skipped] PASSED [ 93%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_changed_paths_success[cancelled] PASSED [ 95%]
tests/unit/test_summary_gate_requires_success.py::test_non_frontend_pr_still_passes_with_e2e_skipped PASSED [ 96%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[failure] PASSED [ 97%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[skipped] PASSED [ 98%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[cancelled] PASSED [100%]

============================= slowest 10 durations =============================
7.78s call     tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement
5.96s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
3.43s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history]
3.30s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases]
3.10s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
2.95s call     tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses
2.72s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct]
2.71s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent]
2.65s setup    tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[summary]
2.64s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct]
======================== 82 passed in 125.58s (0:02:05) ========================

exit code: 0
```

Second consecutive complete run:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 82 items

tests/unit/test_impact_map.py::test_generator_separates_exact_bindings_from_attribute_guesses PASSED [  1%]
tests/unit/test_impact_map.py::test_route_query_reaches_handler_and_reports_module_blindness PASSED [  2%]
tests/unit/test_impact_map.py::test_runtime_only_route_is_live_but_has_no_invented_handler PASSED [  3%]
tests/unit/test_impact_map.py::test_reconciliation_static_only_route_is_not_claimed_live PASSED [  4%]
tests/unit/test_impact_map.py::test_same_inputs_generate_byte_identical_json PASSED [  6%]
tests/unit/test_impact_map.py::test_fresh_build_conserves_calls_and_keeps_coarse_edges_blind PASSED [  7%]
tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates PASSED [  8%]
tests/unit/test_impact_map.py::test_refresh_rejects_unavailable_recall_history PASSED [  9%]
tests/unit/test_impact_map.py::test_refresh_refuses_to_overwrite_committed_inputs PASSED [ 10%]
tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[module-to-package] PASSED [ 12%]
tests/unit/test_impact_map.py::test_refresh_reports_current_path_mismatches_as_misses[remove-modules] PASSED [ 13%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target0-symlink] PASSED [ 14%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-recall.json-target1-symlink] PASSED [ 15%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map-currency.json-target2-symlink] PASSED [ 17%]
tests/unit/test_impact_map.py::test_refresh_rejects_output_aliases_before_writes[impact-map.json-target3-hardlink] PASSED [ 18%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[scripts/impact_map.py] PASSED [ 19%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_to_repository_files[tests/unit/test_impact_map.py] PASSED [ 20%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[map] PASSED [ 21%]
tests/unit/test_impact_map.py::test_refresh_rechecks_aliases_at_write_time[summary] PASSED [ 23%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[symlink] PASSED [ 24%]
tests/unit/test_impact_map.py::test_write_json_does_not_follow_links[hardlink] PASSED [ 25%]
tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map PASSED [ 26%]
tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement PASSED [ 28%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-direct] PASSED [ 29%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-recall-direct] PASSED [ 30%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct] PASSED [ 31%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct] PASSED [ 32%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct] PASSED [ 34%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-direct] PASSED [ 35%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-recall-direct] PASSED [ 36%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-direct] PASSED [ 37%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[committed-map-symlink] PASSED [ 39%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-symlink] PASSED [ 40%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink] PASSED [ 41%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[generated-map-hardlink] PASSED [ 42%]
tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[currency-absent] PASSED [ 43%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[missing-history] PASSED [ 45%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-cases] PASSED [ 46%]
tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle] PASSED [ 47%]
tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses PASSED [ 48%]
tests/unit/test_impact_map.py::test_committed_map_has_nonempty_queryable_blind_spots_and_route_anchor PASSED [ 50%]
tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[False] PASSED [ 51%]
tests/unit/test_summary_gate_requires_success.py::test_impact_map_failure_never_authorizes_auto_revert[True] PASSED [ 52%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-pull_request-refs/pull/1/merge] PASSED [ 53%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/main] PASSED [ 54%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/heads/dev] PASSED [ 56%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-push-refs/tags/v1.0.0] PASSED [ 57%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[success-workflow_dispatch-refs/heads/main] PASSED [ 58%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-pull_request-refs/pull/1/merge] PASSED [ 59%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/main] PASSED [ 60%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/heads/dev] PASSED [ 62%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-push-refs/tags/v1.0.0] PASSED [ 63%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[failure-workflow_dispatch-refs/heads/main] PASSED [ 64%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-pull_request-refs/pull/1/merge] PASSED [ 65%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/main] PASSED [ 67%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/heads/dev] PASSED [ 68%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-push-refs/tags/v1.0.0] PASSED [ 69%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[skipped-workflow_dispatch-refs/heads/main] PASSED [ 70%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-pull_request-refs/pull/1/merge] PASSED [ 71%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/main] PASSED [ 73%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/heads/dev] PASSED [ 74%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-push-refs/tags/v1.0.0] PASSED [ 75%]
tests/unit/test_summary_gate_requires_success.py::test_summary_requires_impact_map_success_on_every_trigger[cancelled-workflow_dispatch-refs/heads/main] PASSED [ 76%]
tests/unit/test_summary_gate_requires_success.py::test_positive_control_all_success_passes PASSED [ 78%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[failure] PASSED [ 79%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[skipped] PASSED [ 80%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_e2e_success_not_merely_absence_of_failure[cancelled] PASSED [ 81%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_the_other_hard_gated_lanes_too[fast-tests-kwargs0] PASSED [ 82%]
tests/unit/test_summary_gate_requires_success.py::test_main_push_requires_the_other_hard_gated_lanes_too[frontend-build-kwargs1] PASSED [ 84%]
tests/unit/test_summary_gate_requires_success.py::test_integration_stays_advisory_on_main PASSED [ 85%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[failure] PASSED [ 86%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[skipped] PASSED [ 87%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_fast_tests_success[cancelled] PASSED [ 89%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[failure] PASSED [ 90%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[skipped] PASSED [ 91%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_frontend_build_success[cancelled] PASSED [ 92%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_changed_paths_success[skipped] PASSED [ 93%]
tests/unit/test_summary_gate_requires_success.py::test_pull_request_requires_changed_paths_success[cancelled] PASSED [ 95%]
tests/unit/test_summary_gate_requires_success.py::test_non_frontend_pr_still_passes_with_e2e_skipped PASSED [ 96%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[failure] PASSED [ 97%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[skipped] PASSED [ 98%]
tests/unit/test_summary_gate_requires_success.py::test_concurrency_pr_requires_e2e_success[cancelled] PASSED [100%]

============================= slowest 10 durations =============================
9.40s call     tests/unit/test_impact_map.py::test_committed_recall_gate_rejects_collapse_and_accepts_improvement
7.34s call     tests/unit/test_impact_map.py::test_refresh_publishes_currency_without_requiring_contributor_updates
6.07s call     tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
4.81s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[source-direct]
4.49s setup    tests/unit/test_impact_map.py::test_failed_refresh_invalidates_previous_currency[invalid-oracle]
4.43s setup    tests/unit/test_impact_map.py::test_skill_refresh_recipe_publishes_and_queries_fresh_map
4.39s call     tests/unit/test_impact_map.py::test_committed_recall_report_is_reproducible_and_keeps_misses
4.28s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-hardlink]
3.28s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[cases-direct]
3.13s setup    tests/unit/test_impact_map.py::test_refresh_rejects_summary_aliases_before_any_write[oracle-direct]
======================== 82 passed in 149.15s (0:02:29) ========================

exit code: 0
```

OBSERVED additional regression check of the existing auto-revert confirmation
suite: `python3 -m pytest tests/unit/test_auto_revert_confirms_before_reverting.py -p no:randomly`:

```text
pytest temp base: $TMPDIR/cwng-pytest ($TMPDIR is mounted and writable)
============================= test session starts ==============================
platform darwin -- Python 3.12.7, pytest-9.0.3, pluggy-1.6.0 -- $VENV/bin/python
rootdir: $ROOT
configfile: pytest.ini
plugins: mock-3.15.1, Faker-40.15.0, flask-1.3.0, cov-7.1.0, xdist-3.8.0, timeout-2.4.0, Flask-Dance-7.1.0, requests-mock-1.12.1, anyio-4.13.0
collecting ... collected 7 items

tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_single_failure_does_not_revert PASSED [ 14%]
tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_single_failure_triggers_a_rerun_of_the_same_tree PASSED [ 28%]
tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_failure_that_reproduces_is_reverted PASSED [ 42%]
tests/unit/test_auto_revert_confirms_before_reverting.py::test_the_second_attempt_does_not_rerun_again PASSED [ 57%]
tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_rerun_that_cannot_be_requested_fails_closed PASSED [ 71%]
tests/unit/test_auto_revert_confirms_before_reverting.py::test_the_revert_pr_step_is_gated_on_confirmation PASSED [ 85%]
tests/unit/test_auto_revert_confirms_before_reverting.py::test_confirmation_does_not_widen_workflow_permissions PASSED [100%]

============================= slowest 10 durations =============================
0.25s call     tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_single_failure_does_not_revert
0.24s call     tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_rerun_that_cannot_be_requested_fails_closed
0.22s call     tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_single_failure_triggers_a_rerun_of_the_same_tree
0.09s setup    tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_single_failure_does_not_revert
0.04s call     tests/unit/test_auto_revert_confirms_before_reverting.py::test_the_second_attempt_does_not_rerun_again
0.04s call     tests/unit/test_auto_revert_confirms_before_reverting.py::test_a_failure_that_reproduces_is_reverted
0.03s call     tests/unit/test_auto_revert_confirms_before_reverting.py::test_confirmation_does_not_widen_workflow_permissions
0.02s call     tests/unit/test_auto_revert_confirms_before_reverting.py::test_the_revert_pr_step_is_gated_on_confirmation

(2 durations < 0.005s hidden.  Use -vv to show these durations.)
============================== 7 passed in 1.27s ===============================

exit code: 0
```

### Final changelog and workflow lint

OBSERVED: the revised fragment at `changelog.d/impact-map-currency.md:3`
describes advisory source-layout misses, the required error gate, the automatic
revert exclusion, guarded publication, and the retained recall floor. The broad
contributor guarantee was removed. Both `scripts/` and `.agents/` are non-exempt;
the fragment is required and retained.

`python3 scripts/check_changelog_diff.py origin/main HEAD`:

```text
CHANGELOG integrity guard passed: the entry requirement is satisfied or every changed path is non-shipping, and no PR-authored release structure was lost.

exit code: 0
```

OBSERVED: `actionlint .github/workflows/tests.yml .github/workflows/auto-revert.yml`
produced no output and exited **0**. The first lint run on auto-revert reported an
existing `SC2086` unquoted filename-list expansion in its logging path. The
quoted print/indent pipeline at `.github/workflows/auto-revert.yml:116` resolves
that warning without suppressing lint or changing the revert decision.
`git diff --check` also exits 0.

OBSERVED scope: the code/tests, skill, usage documentation, evidence, changelog,
and PR description were updated for the five HOLD findings. No existing test
or assertion from the prior head was removed or weakened. No runtime dependency
was added; test execution and reproduction history use no network.

Not done: no new held-out recall study, no generator-mutation harness rerun,
no application/UI/container testing, no deliberately failing hosted workflow,
no live automatic revert, merge, release, or upstream push. The earlier mutation
observations remain historical, not new measurements. This pass demonstrates
the named refactors and write failures; it does not claim that every possible
cps change, generator failure, or infrastructure failure will pass CI. Individual
file replacement is not an atomic directory transaction or a concurrency lock.
