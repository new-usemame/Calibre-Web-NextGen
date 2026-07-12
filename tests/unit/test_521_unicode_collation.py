import sqlite3

import pytest

from cps.unicode_collation import unicode_initial, unicode_sort_key


@pytest.mark.parametrize(
    ("value", "key", "initial"),
    [
        (None, None, None),
        ("", "", ""),
        (0, None, None),
        (b"E", None, None),
        ([], None, None),
        ({}, None, None),
        ("Èclair", "eclair", "E"),
        ("E\u0300clair", "eclair", "E"),
        ("Ñandú", "n\uffffandu", "Ñ"),
        ("Straße", "strasse", "S"),
    ],
)
def test_collation_contract(value, key, initial):
    assert unicode_sort_key(value) == key
    assert unicode_initial(value) == initial


def test_real_sqlite_udfs_sort_group_and_filter():
    conn = sqlite3.connect(":memory:")
    conn.create_function("ng_sort_key", 1, unicode_sort_key)
    conn.create_function("ng_initial", 1, unicode_initial)
    conn.execute("create table items (id integer, value text)")
    values = [None, "", "Zulu", "Ñandú", "Nube", "Èclair", "Eclair", "E\u0300cole"]
    conn.executemany("insert into items values (?, ?)", enumerate(values, 1))

    ordered = [r[0] for r in conn.execute(
        "select value from items order by ng_sort_key(value), value, id"
    )]
    assert ordered.index("Nube") < ordered.index("Ñandú") < ordered.index("Zulu")

    e_group = [r[0] for r in conn.execute(
        "select value from items where ng_initial(value) = 'E' "
        "order by ng_sort_key(value), value, id"
    )]
    assert set(e_group) == {"Èclair", "Eclair", "E\u0300cole"}

    buckets = [r[0] for r in conn.execute(
        "select ng_initial(value) from items "
        "where ng_initial(value) is not null and ng_initial(value) <> '' "
        "group by ng_initial(value)"
    )]
    assert buckets.count("E") == 1
