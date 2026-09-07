# P0.2b evidence — impact map currency

All observations below are local unless explicitly described as hosted CI. Commands shown as
`python3` used the repo virtualenv; pytest ran through that same interpreter with
`-m pytest`. Paths in captured output are normalized to `$ROOT`, `$TMPDIR`, and
`$VENV`; synthetic temporary usernames are normalized to `fixture-user`.
No network access is used by these tests. Base: `e8c512278f`.

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
Missing historical evidence fails at `:1267`; overwriting committed artifact
inputs is refused at `:1259`. There is no blanket continue-on-error.

This shape satisfies the contributor constraint: a cps-only PR does not need to
commit generated JSON. Every run publishes its own fresh graph/report and visible
currency; committed snapshots remain a maintainer responsibility. CI never pushes
or opens automated update PRs. Read the checked SHA in the summary when using a PR
artifact, because the PR checkout can be a merge candidate.

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
It imposes no fixed recall percentage or miss count.

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
