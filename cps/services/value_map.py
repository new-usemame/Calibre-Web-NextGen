# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin-managed value map for list metadata (#2438, Discussion #2481).

One stored map, read the same way wherever values come in. A rule matches an
incoming value case- and accent-insensitively and turns it into zero or more
target values: one target renames, several split, none drops. A target is also
the canonical spelling of itself, so "thriller" becomes "Thriller" once a rule
names "Thriller" as a target. Values no rule covers are kept as they are.

This module holds the matching and proposes library changes; writing them is
the change set's job (``services/change_sets.py``). It does not decide where
the map is applied; callers do.
"""
import unicodedata
from dataclasses import dataclass, field as dc_field

from sqlalchemy import func

from .. import db
from .fields import FIELD_SPECS


def fold(value):
    """Matching key: accents stripped, case folded, inner whitespace collapsed."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def clean_rules(field, raw):
    """Validate a rule list from the API or an import.

    ``raw`` is a list of ``{"incoming": str, "targets": [str, ...]}`` or a
    mapping ``{incoming: [targets]}`` (the shape of a JSON export). Returns
    ``(rules, errors)``; ``rules`` is a list of dicts with ``incoming``,
    ``incoming_key`` and ``targets``.
    """
    spec = FIELD_SPECS.get(field)
    if spec is None:
        return [], ["unknown field"]
    if isinstance(raw, dict):
        raw = [{"incoming": k, "targets": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return [], ["rules must be a list or an object"]
    rules, errors, seen = [], [], {}
    for index, item in enumerate(raw):
        incoming = item.get("incoming") if isinstance(item, dict) else None
        targets = item.get("targets") if isinstance(item, dict) else None
        if isinstance(targets, str):
            targets = [targets]
        if not isinstance(incoming, str) or not incoming.strip():
            errors.append(f"rule {index + 1}: the incoming value is empty")
            continue
        if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
            errors.append(f"rule {index + 1}: targets must be a list of text values")
            continue
        incoming = " ".join(incoming.split())
        targets = [" ".join(t.split()) for t in targets if t.strip()]
        if spec.forbid_comma and any("," in v for v in [incoming, *targets]):
            errors.append(f"rule {index + 1}: a value cannot contain a comma")
            continue
        key = fold(incoming)
        if key in seen:
            errors.append(f"rule {index + 1}: '{incoming}' repeats rule {seen[key] + 1}")
            continue
        seen[key] = index
        deduped = []
        for target in targets:
            if fold(target) not in {fold(t) for t in deduped}:
                deduped.append(target)
        if not spec.multi and len(deduped) > 1:
            errors.append(f"rule {index + 1}: a single-valued field maps to one value or none")
            del seen[key]
            continue
        rules.append({"incoming": incoming, "incoming_key": key, "targets": deduped})
    return rules, errors


@dataclass
class ValueMap:
    rules: dict = dc_field(default_factory=dict)       # incoming_key -> [targets]
    canonical: dict = dc_field(default_factory=dict)   # fold(target) -> target

    @classmethod
    def from_rules(cls, rules):
        vm = cls()
        for rule in rules:
            vm.rules[rule["incoming_key"]] = list(rule["targets"])
            for target in rule["targets"]:
                vm.canonical.setdefault(fold(target), target)
        return vm

    def covers(self, value):
        key = fold(value)
        return key in self.rules or key in self.canonical

    def map_values(self, values):
        """Map a book's values, order kept.

        A value a rule produced (or re-cased to its canonical target) is
        de-duplicated case- and accent-insensitively. A value no rule covers
        is left as it is, so only an exact repeat of it is dropped: merging
        "Montreal" with "Montréal" is a rule's job, not a side effect.
        """
        out, folded, exact = [], set(), set()
        for value in values:
            key = fold(value)
            if key in self.rules:
                produced = self.rules[key]
            elif key in self.canonical:
                produced = [self.canonical[key]]
            else:
                if value not in exact:
                    exact.add(value)
                    out.append(value)
                continue
            for item in produced:
                item_key = fold(item)
                if item_key not in folded:
                    folded.add(item_key)
                    exact.add(item)
                    out.append(item)
        return out


def apply_value_map(field, values, app_session=None, allowed=None):
    """Map ``values`` for ``field`` with the stored rules: ``(kept, rejected)``.

    The single entry point every caller uses (clean-up preview, ingest,
    automatic and manual fetch). ``allowed`` is the allow set when "only allow
    mapped or existing values" is on (the library's values; every map target
    is added here, since library values are pruned when unused). A value no
    rule matched and not allowed is rejected; a value a rule drops is not, it
    has already been ruled on. With ``allowed`` None nothing is rejected.
    """
    if app_session is None:
        from .. import ub
        app_session = ub.session
    value_map = load_map(app_session, field)
    if allowed is None:
        return value_map.map_values(values), []
    allow_keys = {fold(v) for v in allowed} | set(value_map.canonical)
    kept, rejected = [], []
    for value in values:
        key = fold(value)
        if key in value_map.rules or key in allow_keys:
            kept.append(value)
        else:
            rejected.append(value)
    return value_map.map_values(kept), rejected


def load_map(app_session, field):
    from .. import ub
    rows = (app_session.query(ub.MetadataValueRule)
            .filter(ub.MetadataValueRule.field == field)
            .order_by(ub.MetadataValueRule.incoming_key).all())
    return ValueMap.from_rules(
        {"incoming_key": r.incoming_key, "targets": r.targets or []} for r in rows
    )


def _book_values(book, field):
    return [row.name for row in getattr(book, FIELD_SPECS[field].link_attr)]


def library_values(calibre_session, field):
    """Every value in the library with its book count, for the admin list."""
    spec = FIELD_SPECS[field]
    column = spec.link_table.c[spec.link_column]
    rows = (calibre_session.query(spec.model.name, func.count(column))
            .outerjoin(spec.link_table, column == spec.model.id)
            .group_by(spec.model.id).all())
    return sorted(({"name": name, "count": count} for name, count in rows),
                  key=lambda item: (-item["count"], fold(item["name"])))


def plan(calibre_session, value_map, field, book_ids=None):
    """Rows for a change set: books whose values the map would change. Nothing is written."""
    query = calibre_session.query(db.Books)
    if book_ids is not None:
        query = query.filter(db.Books.id.in_(list(book_ids)))
    changes = []
    for book in query.order_by(db.Books.sort).all():
        before = _book_values(book, field)
        after = value_map.map_values(before)
        if after != before:
            changes.append({"book_id": book.id, "field": field, "old": before, "new": after})
    return changes
