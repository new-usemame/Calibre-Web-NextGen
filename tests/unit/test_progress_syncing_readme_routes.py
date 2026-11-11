# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The progress-sync README's route table matches the routes the app serves.

Client authors read cps/progress_syncing/README.md to learn the KOSync wire.
It documented 6 of the blueprint's 26 routes, so the delivery, deletion,
library, pairing and sleep-screen endpoints existed only in the code. This
compares the README's "Routes" table against the kosync blueprint's real
url_map: a route added without a row, a row for a route that is gone, or a
wrong method fails here.
"""

import importlib
import re
from pathlib import Path

from flask import Flask

REPO = Path(__file__).resolve().parents[2]
README = REPO / "cps" / "progress_syncing" / "README.md"

_ROW = re.compile(r"^\|\s*`(GET|PUT|POST|DELETE|PATCH)`\s*\|\s*`(/[^`]*)`\s*\|")
_CONVERTER = re.compile(r"<(?:[a-z_]+:)?([a-z_]+)>")


def _normalise(rule):
    # `<int:delivery_id>` in Flask reads as `<delivery_id>` in the README.
    return _CONVERTER.sub(r"<\1>", rule)


def _readme_routes():
    text = README.read_text(encoding="utf-8")
    section = text.split("\n## Routes\n", 1)
    assert len(section) == 2, "README has no '## Routes' section"
    body = section[1].split("\n## ", 1)[0]
    routes = set()
    for line in body.splitlines():
        match = _ROW.match(line.strip())
        if match:
            routes.add((match.group(1), match.group(2)))
    return routes


def _served_routes():
    # The package re-exports the blueprint under the module's own name, so
    # import the module itself; importing it registers every route file.
    module = importlib.import_module("cps.progress_syncing.protocols.kosync")

    app = Flask(__name__)
    app.register_blueprint(module.kosync)
    routes = set()
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("kosync."):
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            routes.add((method, _normalise(rule.rule)))
    return routes


def test_readme_route_table_lists_exactly_the_served_routes():
    served = _served_routes()
    documented = _readme_routes()
    assert served, "kosync blueprint registered no routes; the comparison would be vacuous"
    undocumented = sorted(served - documented)
    stale = sorted(documented - served)
    assert not undocumented and not stale, (
        f"undocumented routes: {undocumented}; README rows with no route: {stale}"
    )
