---
name: cwng-impact-map
description: Query Calibre-Web-NextGen's static cps impact map before changing a Python file, symbol, or Flask route, including confidence and blind-spot data.
---

# CWNG impact map

Use this skill when a change under `cps/` needs a fast, evidence-linked impact
survey. The map is a recall-oriented hint, not an authority.

From the repository root, query the most specific target available:

```bash
python3 scripts/impact_map.py query 'cps/module.py:module_level_symbol'
python3 scripts/impact_map.py query 'blueprint.endpoint'
python3 scripts/impact_map.py query '/literal/or/<converter:rule>'
```

Use `--format json` when another tool will consume the result, and increase
`--depth` only when the direct neighborhood is insufficient.

Report these together:

1. `reached_by` sites, especially `live_routes_reaching_target`;
2. downstream `reaches` sites;
3. confidence on every cited hop; and
4. `blind_spots.count`, the leading reasons, and relevant `file:line` records.

Treat `exact_local_symbol`, `exact_import_symbol`,
`exact_import_module_attribute`, and `route_reconciled` as high-confidence
static evidence. Treat `class_member_coarse` and `attribute_name_guess` as
leads that require source inspection. Never turn absence from the result into a
claim of no impact.

Compare `generated_from.cps_tree_sha` with `git rev-parse HEAD:cps`. If those
differ, regenerate and run the historical check before querying (a differing
checkout SHA alone may just be the tooling commit that contains the map):

```bash
python3 scripts/impact_map.py build
python3 scripts/impact_map.py recall
```

## Two limits you must state when you use this

**1. The map stops at Python. It has zero nodes outside `cps/*.py`** — no
templates, no frontend. Measured over the last 400 `cps`-touching commits, **35%
also changed `cps/templates/*` (10%) or `frontend/src/*` (29%)**. So for roughly
a third of real changes the map structurally cannot name a consumer that exists.
If you change a serializer, a route's response shape, or anything a page renders,
the React or Jinja consumer will not appear — its absence is not evidence.

**2. Recall depends entirely on what you ask about.** The headline historical
figure is ~80%, and that is measured on caller/consumer pairs where both ends are
module-level Python symbols. Raw co-change recall across all changed symbols is
**12.49%**, with 37% of changed symbols getting nothing named at all. The honest
range is therefore **[12.5%, 80%]**, and which end you are at depends on your
target.

You do not have to guess which: **every query prints the matched module's
unresolved-call count and reasons**, and that number is informative rather than
constant — across the 168 modules with 20 or more call sites it ranges from 0.000
to 0.870 (p10 0.262, median 0.440, p90 0.658). A module reported at 87%
unresolved is telling you the answer is thin; one at 15% is telling you it is
solid. Read that number every time and report it alongside the result.

Read `docs/impact-map.md` only when updating the generator, route oracle,
confidence taxonomy, or historical case set.
