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


## Review fixes: summary corruption, failed refresh reuse, and required gate

OBSERVED: new regression tests were committed before implementation in
`b6ff4faf0c`; fixes and user documentation were committed in `a737b01b0d`.
Existing tests and assertions were retained. References in this section use the
fixed source at `a737b01b0d` unless a historical revision is explicitly named.

### 1. Reject summary aliases before any write

OBSERVED code: `scripts/impact_map.py:1262` protects the committed map/recall,
route oracle, historical cases, parsed Python inputs, and all three output files.
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
protection relying on Test Suite Summary could merge them. Advisory staleness
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
