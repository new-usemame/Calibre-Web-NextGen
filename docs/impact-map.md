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
8 of 10 affected sites: **80.00% recall**.

The two misses are retained in the report:

1. a Python API response changed with its TypeScript consumer, which is outside
   the map's `cps` Python node scope;
2. a dependency propagated through an instance-method call and a
   keyword-controlled branch, where receiver identity is not statically known.

The report verifies that every named commit exists locally, that its diff
actually touches both declared evidence paths, and records the actual graph
path and confidence for hits. Re-run it whenever the map changes; do not
preserve the percentage by deleting misses.
