# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""What each list metadata field is, in one place.

The value map, the change sets and the clean-up API read a field's model,
its link to books and its cardinality from here rather than each keeping
their own copy. A single-valued field (``multi=False``) takes one-to-one or
drop rules only.
"""
from dataclasses import dataclass

from sqlalchemy import Table

from .. import db


@dataclass(frozen=True)
class FieldSpec:
    name: str
    model: type           # calibre table; names are unique case-insensitively (NOCASE)
    link_attr: str        # the Books relationship that holds it
    link_table: Table
    link_column: str
    multi: bool           # False: one value per book
    forbid_comma: bool    # calibre splits these on commas
    has_sort: bool        # the row carries a sort column kept equal to the name

    def new_row(self, name):
        return self.model(name, name) if self.has_sort else self.model(name)


FIELD_SPECS = {
    "tags": FieldSpec("tags", db.Tags, "tags", db.books_tags_link, "tag",
                      multi=True, forbid_comma=True, has_sort=False),
}
