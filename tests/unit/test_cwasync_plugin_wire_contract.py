# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every field the plugin sends must be declared in api.json (#920).

The plugin talks to us through lua-Spore, which does NOT send the table the
caller passes. It rebuilds the request body from exactly the keys named in the
method's ``payload`` list in ``api.json`` and silently drops everything else
(lua-Spore 0.4.2, ``src/Spore.lua``)::

    if method.payload then
        payload = {}
        for i = 1, #method.payload do
            local v = method.payload[i]
            payload[v] = params[v]
        end
    end

So a field can be set by the client, reviewed in the diff, covered by tests on
both sides, and still never reach the server. That is what happened to #906: it
added ``complete``/``complete_source`` to the push and never touched
``api.json``, so lua-Spore dropped both and the delete-sync it shipped was a
no-op on the wire. #920's fix sends a new ``deleted`` field down the same path,
which would have failed the same silent way.

There is no way to see this in review — the two files are far apart and the
failure is silent — so it is pinned here instead. This test reads the real
client source and the real spec, so it fails when they drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PLUGIN = Path(__file__).resolve().parents[2] / "koreader" / "plugins" / "cwasync.koplugin"
CLIENT_LUA = PLUGIN / "CWASyncClient.lua"
API_JSON = PLUGIN / "api.json"

# `self.client:<method>({`  — the opening of the call; the body is then scanned
# by matching braces rather than by regex. A lazy `.*?\}` would stop at the
# first `}` of a nested table and silently miss every key after it, which is
# the exact failure this file exists to prevent.
_CALL_OPEN = re.compile(r"self\.client:(?P<method>\w+)\(\{")
# a `key =` at the top level of that table constructor
_KEY = re.compile(r"^\s*(?P<key>\w+)\s*=", re.MULTILINE)


def _spec():
    return json.loads(API_JSON.read_text(encoding="utf-8"))["methods"]


def _balanced_body(source, start):
    """The text of the table constructor opened at `start` (just past its `{`),
    up to its matching close brace."""
    depth, i = 1, start
    while i < len(source) and depth:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    return source[start:i - 1]


def _top_level_keys(body):
    """`key =` names at depth 0, so a nested table's own keys aren't mistaken
    for fields of the request body. Newlines inside nested tables are kept so
    line-anchored matching still lines up."""
    flat, depth = [], 0
    for char in body:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0 or char == "\n":
            flat.append(char)
    return set(_KEY.findall("".join(flat)))


def _client_calls():
    """{method: {keys the client passes}} straight from the plugin source."""
    source = CLIENT_LUA.read_text(encoding="utf-8")
    calls = {}
    for match in _CALL_OPEN.finditer(source):
        body = _balanced_body(source, match.end())
        calls.setdefault(match.group("method"), set()).update(_top_level_keys(body))
    return calls


def test_the_parser_actually_finds_the_calls():
    """Guards the test itself: a regex that matches nothing would pass every
    assertion below while checking nothing."""
    calls = _client_calls()
    assert set(calls) == {
        "update_progress", "get_progress", "pull_annotations", "push_annotations",
    }
    assert "annotations" in calls["push_annotations"]


def test_a_nested_table_is_parsed_by_depth_not_by_the_first_brace():
    """A nested table must not confuse the parser in either direction: its own
    keys are not request fields, and keys declared after it must still be seen.
    A line-anchored scan over the raw body reports `Accept` here, which would
    fail this contract against an api.json that is perfectly correct."""
    source = """
        return self.client:push_annotations({
            document = document,
            headers = {
                Accept = "application/json",
            },
            deleted = deleted,
        })
    """
    match = _CALL_OPEN.search(source)
    keys = _top_level_keys(_balanced_body(source, match.end()))

    assert keys == {"document", "headers", "deleted"}
    assert "Accept" not in keys, "a nested table's own key is not a request field"


def test_push_annotations_declares_the_delete_fields():
    """The #920 fix rides on these two; undeclared, they never leave the device."""
    payload = _spec()["push_annotations"]["payload"]
    assert "deleted" in payload
    assert "delete_source" in payload


@pytest.mark.parametrize("method", sorted(_spec()))
def test_every_field_the_client_sends_is_declared_in_the_payload(method):
    spec = _spec()[method]
    if "payload" not in spec:
        pytest.skip(f"{method} sends no body")
    sent = _client_calls().get(method, set())
    undeclared = sent - set(spec["payload"])
    assert not undeclared, (
        f"{method} passes {sorted(undeclared)}, which api.json does not list in "
        f"`payload` — lua-Spore will drop them and the server will never see "
        f"them. Add them to api.json's payload list."
    )
