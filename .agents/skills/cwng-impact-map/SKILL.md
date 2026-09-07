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

Read `docs/impact-map.md` only when updating the generator, route oracle,
confidence taxonomy, or historical case set.
