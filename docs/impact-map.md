# Static impact map

`state/modernization/impact-map.json` is a recall-oriented map of Python code
under `cps/`. It answers a useful but deliberately limited question: if this
file, symbol, or route changes, which statically visible sites may be involved?

It is not an authority. An omitted edge is not proof that no dependency exists.
Read the returned blind-spot data before relying on a query.

## Generate and query

From the repository root:

```bash
python3 scripts/impact_map.py build
python3 scripts/impact_map.py query cps/services/reading_position.py:read_resume_position
python3 scripts/impact_map.py query api_v1.get_bookmark
python3 scripts/impact_map.py query '/api/v1/books/<int:book_id>/bookmark'
python3 scripts/impact_map.py query cps/services/reading_position.py --format json
python3 scripts/impact_map.py recall
```

The query accepts a relative `cps/*.py` file, `cps.module:symbol`, bare symbol,
endpoint, URL rule, or exact node ID. Text output shows both directions,
confidence at every hop, live routes reaching the target, and the matched
module's unresolved-site census. JSON output includes every relevant blind-spot
record rather than the text view's sample.

Generation is deterministic for a fixed `cps` source and route oracle. The
artifact's `repo_sha` is the most recent repository commit that touched `cps`,
and `cps_tree_sha` fingerprints the complete source tree. Those values remain
stable when a later tooling-only commit contains the generated file. The
generator parses source with the standard-library AST and never imports or
executes `cps`.

## Currency is measured in CI

The **Impact Map (regeneration + currency)** job in the Test Suite workflow
rebuilds the map and re-evaluates recall on every PR, main/dev push (including
merges), version tag, and manual run. It uses full Git history for the historical
evidence check and only the Python standard library for regeneration.

The job summary reports the checked commit, the current and committed `cps`
tree fingerprints, whether each generated file differs, and the new recall
number with every miss. Its `impact-map-<checked SHA>` download contains
`impact-map.json`, `impact-map-recall.json`, and `impact-map-currency.json`.
Artifacts are retained for 14 days; a manual Test Suite run produces another
copy when needed. On PRs the checked SHA is the checkout's merge candidate,
so use the summary's SHA when identifying the tree that was evaluated.

**Staleness and evaluated recall misses return success.** A source addition,
symbol rename, module relocation, or module removal can change the measurement
without requiring a contributor to commit generated JSON. In particular, the
current paths for a historical case may differ from the paths its frozen commit
touched; that is a reported miss, not unavailable history. CI has read-only
repository permission and publishes fresh
outputs separately; it does not push commits or open update PRs. The committed
files remain a snapshot, and their refresh is a maintainer's task. Download the
fresh CI map (query with `--map`) or regenerate locally before using a stale
snapshot. Generator errors, malformed inputs, empty case sets, and unavailable
historical commits still fail the job;
the required **Test Suite Summary** waits for this job and rejects every result
other than success, including failure, cancellation, or a skipped job. There is
no blanket `continue-on-error` hiding errors. Advisory staleness still passes
that gate.

The Impact Map job is excluded from automatic-revert decisions, along with its
propagated summary failure. An unsuccessful measurement does not establish that
the product commit caused a regression: unavailable history is infrastructure,
and generator/input failures need investigation of the tooling or evidence.
The required summary keeps these failures visible without automatically undoing
an unrelated application change.

To reproduce the job locally, use a separate output directory:

```bash
python3 scripts/impact_map.py refresh \
  --output-dir "$TMPDIR/impact-map" --summary "$TMPDIR/impact-map-summary.md"
```

Summary and generated output destinations must be separate from tracked
repository files (including the generator and tests), the committed map/recall,
oracle, cases, parsed Python sources, and each other. Refresh checks all
destinations before writing and checks again at publication. Resolved paths and
same-file checks catch symbolic links, hard links, and missing-output aliases.
The shared writer replaces individual files rather than writing through a link;
this also protects linked targets used with the build/recall JSON writer.

Repeated refreshes can reuse the output directory. After validating destination
paths, refresh removes the previous currency file before reading or generating
evidence and publishes a new currency file last, after successful validation and
summary writing. A failed attempt may leave diagnostic map/recall files, but
without `impact-map-currency.json` the directory is incomplete and must not be
treated as validated evidence. Repair the error and rerun refresh. This is an
invalidation protocol, not an atomic directory replacement; use separate output
directories for concurrent refreshes.

Currency compares the complete regenerated map and recall report, not only the
`cps` SHA. Generator and route-oracle changes can therefore show drift even
when `cps` is unchanged. Missing generated snapshots count as stale; missing
historical commits are evaluation errors. Historical/current path mismatches
remain miss results with `evidence_paths_present: false` and a reason. Like
`build`, this command should run on a clean checkout: the source is read from
disk while Git supplies its committed provenance.

The unit suite separately builds a miniature graph to check call conservation
and that guessed/coarse edges retain their blind records. This catches a
generator regression while the committed JSON is still unchanged. The committed
recall test checks reproducibility, complete case retention, historical evidence,
and hit/miss accounting, with a one-sided floor of eight hits. A regenerated
report cannot hide a collapsed committed graph, and an improvement to nine hits
passes. This floor applies to the committed snapshot and case set, not the fresh
measurement of a contributor's changed source tree. Re-evaluating that frozen
snapshot does not require its node paths to match the current source layout.

## Graph and confidence

Nodes represent `cps` modules, module-level functions/classes, and routes.
Methods are deliberately folded into their module-level class node. Edges are:

- `call`: a call from the enclosing module-level function/class (or module),
- `import`: an internal import binding, and
- `route_handler`: a route reaching its Python handler.

Every edge contains its originating `file`, `line`, and `column`, plus one of
the confidence values embedded in the JSON's `confidence_taxonomy`:

| Confidence | Meaning |
|---|---|
| `exact_local_symbol` | A bare name resolves to a function/class in the same module. |
| `exact_import_symbol` | An explicit import binding resolves to an internal function/class. |
| `exact_import_module_attribute` | An imported module and attribute chain resolve to an internal symbol. |
| `class_member_coarse` | The class is known, but the method is represented only by its class node. |
| `attribute_name_guess` | Receiver identity is unknown; only a unique final attribute name matched. |
| `route_reconciled` | The current static route matches the pinned static-to-runtime reconciliation. |
| `route_unreconciled` | The current static route is absent from that pinned runtime evidence. |
| `import_internal` | An import statement resolves inside `cps`. |

Import-resolved edges and attribute-name guesses are never merged. A guessed or
class-coarse call remains in `blind_spots` even when a low-confidence edge is
also emitted.

## Why blind spots are first-class data

The measured 227-file census that shaped this tool found approximately 36,554
call sites. Bare-name calls were 44.4%; attribute calls on a bare name were
35.3%; calls on a returned expression were 10.9%; longer attribute chains were
9.3%; and indirect callable expressions were 0.1%. `getattr` appeared at 564
sites (1.5%) as an overlapping subset. Static recall therefore cannot exceed
roughly 80%, and the confident core is nearer 45%.

Known built-in and explicitly imported third-party calls are counted separately
as `known_out_of_scope_calls`; they are resolved as outside the `cps` graph and
are not internal blind spots. The generated artifact records every genuinely
unresolved call with its module,
enclosing caller, file, line, column, call shape, reason, and any bounded
candidate set. `blind_spot_summary.by_module` makes blindness queryable as a
count and fraction for every module. Parse failures, wildcard imports, route
binding failures, and route-oracle drift are data too. A build with an empty
blind-spot list is invalid for this codebase.

As a completeness invariant, `call_sites` is exactly the sum of exact internal
call edges, known out-of-scope calls, and unresolved calls. Guessed/coarse edges
are leads attached to the unresolved partition, not a way to make it disappear.

## Runtime-route anchor

`impact-map-route-oracle.json` vendors the finished route snapshot and
`reconciliation.json` result under oracle ID `4143112e`. Its source SHA is
preserved separately from the current graph SHA. Current routes are matched by
semantic identity rather than stale source line numbers; any current-only or
oracle-only difference becomes a `route_oracle_drift` blind spot.

The pinned runtime union has 528 distinct routes: 521 statically reconciled
routes plus seven runtime-only endpoints. Six are Flask-Dance provider login
and callback routes for the generic, GitHub, and Google providers. The seventh
is Flask's built-in static-file endpoint. They are emitted as live route nodes
with `runtime_only_kind` and `runtime_only_explanation`, but no invented Python
handler edge.

To replace the route input after a new runtime matrix and reconciliation have
been reviewed:

```bash
python3 scripts/impact_map.py pin-routes \
  --static-routes "$STATIC_ROUTES" \
  --reconciliation "$RECONCILIATION" \
  --oracle-id "$ORACLE_ID" \
  --source-repo-sha "$SOURCE_SHA"
python3 scripts/impact_map.py build
python3 scripts/impact_map.py recall
```

`pin-routes` rejects extraction errors and count disagreement before replacing
the portable input.

## Historical recall evidence

`impact-map-recall-cases.json` contains ten distinct historical commits selected
by inspecting their function-context diffs. `impact-map-recall.json` is the
regenerable result and records the evaluated map SHA. The current report found
8 of 10 affected sites: 80.00%.

**Do not quote that 80.00% as a measurement.** The map was built and queried on
the eight symbols that became the eight hits *before* this case file was written,
and the two misses are the two symbols that were not in that batch — so the ratio
is constructed, and `selection_method` in the case file says so. The figure that
carries evidentiary weight is an independent replication on mechanically-selected
held-out commits, the author's ten excluded and the map never consulted during
selection: **65/81 = 80.25%** at depth 2 and 4, and **907/1157 = 78.39%** across
all pairs.

Both numbers describe caller/consumer pairs where **both ends are module-level
Python symbols**. Raw co-change recall across all changed symbols is **12.49%**,
with 37% of changed symbols getting nothing named at all, so the honest range is
**[12.5%, 80%]** and which end applies depends on the target. `SKILL.md` carries
that qualifier because it is what an agent loads; it is repeated here because
this is the file maintainers open when they update the case set, and updating a
case set while believing 80.00% was measured is exactly how the constructed
figure would get re-blessed.

The two misses are retained in the report:

1. a Python API response changed with its TypeScript consumer, which is outside
   the map's `cps` Python node scope;
2. a dependency propagated through an instance-method call and a
   keyword-controlled branch, where receiver identity is not statically known.

The report verifies that every named commit exists locally, that its diff
actually touches both declared evidence paths, and records the actual graph
path and confidence for hits. Re-run it whenever the map changes; do not
preserve the percentage by deleting misses.
