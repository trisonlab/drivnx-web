"""Every metric a database's fast profile saves, against values worked out by hand,
CFX-APP-postgres AC-25 (DRIVN-54).

Each expected value below comes from reading the rows in `OBJECTS`, and PostgreSQL's own rules for
its types, not from running the code. Distinct values are counted on PostgreSQL's text form of
each value, and only NULL is missing (the plan agreed for the fast profile). A column that isn't
text gets its counts only: no lengths and no masked patterns (the spec's Assumptions).
"""

import time
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from ..integration.test_postgres_objects_api import DETAILS, objects_in

SCHEMA = "drivnx_golden"
# The UUID the documents table writes three ways, here without its hyphens: 32 hex digits, which
# a secret scan takes for a key.
UUID_BARE = "a0eebc999c0b4ef8bb6d6bb9bd380a11"  # pragma: allowlist secret, ai-skills: allow
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    # Dates and times, with and without a time zone.
    f"""CREATE TABLE {SCHEMA}.moments (
        id integer PRIMARY KEY, instant timestamptz, wall timestamp, day date, clock time,
        clock_zoned timetz
    )""",
    f"""INSERT INTO {SCHEMA}.moments VALUES
        (1, '2024-03-10 12:00:00+00', '2024-03-10 12:00:00', '2024-03-10', '12:00', '12:00+00'),
        (2, '2024-03-10 13:00:00+01', '2024-03-10 12:00:00', '2024-03-10', '12:00:00.5',
            '13:00+01'),
        (3, NULL, NULL, NULL, NULL, NULL),
        (4, 'infinity', '-infinity', 'infinity', '24:00', '23:59:59.999999+14'),
        (5, '0044-03-15 12:00:00+00 BC', '2024-03-10 12:00:00.000001', '0044-03-15 BC', '00:00',
            '00:00-12')""",
    f"ANALYZE {SCHEMA}.moments",
    # Text in several scripts, written with escapes so the composed and decomposed é stay as they
    # are: U&'\00E9' is é in one code point, U&'e\0301' an e and a combining acute accent.
    # varchar, bounded and not, is text as much as text is.
    f"""CREATE TABLE {SCHEMA}.words (
        id integer PRIMARY KEY, name text, code char(3), label varchar(10), free varchar
    )""",
    f"""INSERT INTO {SCHEMA}.words VALUES
        (1, U&'Zo\\00E9', 'ab', 'A-1', 'x y'),
        (2, U&'Zoe\\0301', 'ab ', 'a-1', 'x  y'),
        (3, U&'\\674E\\660E', U&'\\04169', 'b-22', NULL),
        (4, '', NULL, NULL, NULL),
        (5, '  ', U&'\\0663\\00BD\\2167', NULL, NULL),
        (6, NULL, 'x', NULL, NULL),
        (7, repeat(U&'\\0416', 45), NULL, 'A-1', '7')""",
    f"ANALYZE {SCHEMA}.words",
    # Numbers. 9007199254740993 is 2**53 + 1, which a double can't hold apart from 2**53.
    f"""CREATE TABLE {SCHEMA}.numbers (
        id integer PRIMARY KEY, whole integer, big bigint, exact numeric, scaled numeric(5, 2),
        single real, double double precision
    )""",
    f"""INSERT INTO {SCHEMA}.numbers VALUES
        (1, 1, 9007199254740992, 1, 1, 0.1, 0.3),
        (2, 2, 9007199254740993, 1.0, 1.0, 0.1, 0.30000000000000004),
        (3, 2, NULL, 1.00, 1.005, '-0', 'NaN'),
        (4, NULL, -9223372036854775808, 'NaN', 'NaN', '0', 'Infinity'),
        (5, -2147483648, 9007199254740992, NULL, NULL, 'NaN', '-Infinity'),
        (6, 0, 0, 0.000, -999.99, 'Infinity', NULL)""",
    f"ANALYZE {SCHEMA}.numbers",
    # Booleans, JSON both ways, and UUIDs, each written in more than one way.
    f"""CREATE TABLE {SCHEMA}.documents (
        id integer PRIMARY KEY, flag boolean, doc json, doc_binary jsonb, ref uuid
    )""",
    f"""INSERT INTO {SCHEMA}.documents VALUES
        (1, 'yes', '{{"b":1, "a":2}}', '{{"b":1, "a":2}}', 'A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11'),
        (2, 'on', '{{"a":2,"b":1}}', '{{"a":2,"b":1}}', '{{a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11}}'),
        (3, 'off', '{{"a":1,"a":2}}', '{{"a":1,"a":2}}', '{UUID_BARE}'),
        (4, NULL, 'null', 'null', NULL),
        (5, 'f', ' [1, 2.0] ', ' [1, 2.0] ', '00000000-0000-0000-0000-000000000000'),
        (6, 't', NULL, '{{"a": 2}}', 'B0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11')""",
    f"ANALYZE {SCHEMA}.documents",
    # Arrays and binary. The backslashes are doubled for Python: PostgreSQL gets '\x616263', the
    # hex input for the bytes of `abc`.
    f"""CREATE TABLE {SCHEMA}.collections (
        id integer PRIMARY KEY, numbers integer[], tags text[], blob bytea
    )""",
    f"""INSERT INTO {SCHEMA}.collections VALUES
        (1, '{{1,2}}', '{{a,b}}', 'abc'),
        (2, '{{1, 2}}', '{{"a","b"}}', '\\x616263'),
        (3, '{{}}', '{{}}', ''),
        (4, '{{NULL}}', '{{NULL}}', NULL),
        (5, '[0:1]={{1,2}}', '{{"a b",c}}', '\\xDEADBEEF'),
        (6, '{{{{1,2}},{{3,4}}}}', NULL, '\\xdeadbeef')""",
    f"ANALYZE {SCHEMA}.collections",
    # Missing and repeated values, and rows that are exact copies, which only a full profile
    # counts. No key, so the copies can be.
    f"CREATE TABLE {SCHEMA}.repeats (city text, visits integer, note text)",
    f"""INSERT INTO {SCHEMA}.repeats VALUES
        ('Paris', 1, NULL), ('Paris', 1, NULL), ('Paris', 1, NULL), ('Lyon', 2, NULL),
        ('Lyon', NULL, NULL), (NULL, NULL, NULL), (NULL, NULL, NULL), ('Nice', 3, NULL)""",
    f"ANALYZE {SCHEMA}.repeats",
    # 100,000 distinct values. Never analysed, and autovacuum can't, so the estimate is unknown and
    # the table is read whole: an estimate just above 100,000 would sample it by page instead.
    f"CREATE TABLE {SCHEMA}.many (n integer, code text) WITH (autovacuum_enabled = false)",
    f"INSERT INTO {SCHEMA}.many SELECT n, 'c' || n FROM generate_series(1, 100000) AS n",
    # The other kinds of object. A partitioned table's rows are in its partitions, which aren't
    # listed themselves; a materialized view and a view read from it.
    f"CREATE TABLE {SCHEMA}.visits (year integer, name text) PARTITION BY RANGE (year)",
    *(
        f"""CREATE TABLE {SCHEMA}.visits_{year} PARTITION OF {SCHEMA}.visits
            FOR VALUES FROM ({year}) TO ({year + 1})"""
        for year in (2023, 2024)
    ),
    f"""INSERT INTO {SCHEMA}.visits VALUES
        (2023, 'Ann'), (2023, 'Bob'), (2024, 'Ann'), (2024, NULL)""",
    f"ANALYZE {SCHEMA}.visits",
    f"""CREATE MATERIALIZED VIEW {SCHEMA}.visit_names AS
        SELECT name, count(*) AS seen FROM {SCHEMA}.visits GROUP BY name""",
    f"ANALYZE {SCHEMA}.visit_names",
    f"""CREATE VIEW {SCHEMA}.named_visits AS
        SELECT name, year FROM {SCHEMA}.visits WHERE name IS NOT NULL""",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO drivnx_fixture",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO drivnx_fixture",
)


def pattern(text: str, count: int, cut: bool = False) -> dict:
    return {"pattern": text, "count": count, "cut": cut}


def counts(
    missing: int, missing_percent: float, distinct: int, distinct_percent: float | None
) -> dict:
    """A column's missing and distinct counts and their percentages, worked out by hand: missing of
    every row read, distinct of the values that aren't missing, as a CSV's, to 2 places. With
    nothing that isn't missing, there's no distinct percentage."""
    saved = {
        "missing_count": missing,
        "missing_percent": missing_percent,
        "distinct_count": distinct,
    }
    if distinct_percent is not None:
        saved["distinct_percent"] = distinct_percent
    return saved


EXPECTED = {
    "moments": {
        "rows_read": 5,
        "columns": {
            "id": counts(missing=0, missing_percent=0.0, distinct=5, distinct_percent=100.0),
            # Rows 1 and 2 are the same instant written with two offsets: one value. Row 3 is NULL;
            # 4 is infinity and 5 a date BC, so 3 of 4.
            "instant": counts(missing=1, missing_percent=20.0, distinct=3, distinct_percent=75.0),
            # Rows 1 and 2 are the same; 5 is a microsecond later, and 4 is -infinity: 3 of 4.
            "wall": counts(missing=1, missing_percent=20.0, distinct=3, distinct_percent=75.0),
            # Rows 1 and 2 are the same day; infinity and 44 BC are the others: 3 of 4.
            "day": counts(missing=1, missing_percent=20.0, distinct=3, distinct_percent=75.0),
            # 12:00, half a second later, 24:00 (not midnight) and midnight: 4 of 4.
            "clock": counts(missing=1, missing_percent=20.0, distinct=4, distinct_percent=100.0),
            # 12:00+00 and 13:00+01 are the same instant, but PostgreSQL keeps the zone in a timetz,
            # and they aren't equal: 4 of 4.
            "clock_zoned": counts(
                missing=1, missing_percent=20.0, distinct=4, distinct_percent=100.0
            ),
        },
    },
    "words": {
        "rows_read": 7,
        "columns": {
            "id": counts(missing=0, missing_percent=0.0, distinct=7, distinct_percent=100.0),
            # Only row 6 is missing: the empty and the all-space names are values.
            # Lengths in code points: Zoé composed 3, decomposed 4, 李明 2, empty 0, two spaces 2,
            # and 45 Ж, so 56 over 6. The two Zoés are different text and the same pattern, the
            # combining accent going with its letter. Spaces stay as they are. The 45 Ж are cut at
            # 40. Ordered by count, then by pattern: '' < '  ' < 'AA' < 'AAAA…'.
            "name": {
                **counts(missing=1, missing_percent=14.29, distinct=6, distinct_percent=100.0),
                "shortest_length": 0,
                "longest_length": 45,
                "average_length": 9.33,
                "masked_patterns": [
                    pattern("AAA", 2),
                    pattern("", 1),
                    pattern("  ", 1),
                    pattern("AA", 1),
                    pattern("A" * 40, 1, cut=True),
                ],
            },
            # PostgreSQL's text for a char(3) drops its padding, as its own length() does: rows 1
            # and 2 are both `ab`. Rows 4 and 7 are missing. Ж9, then ٣½Ⅷ, numbers in three
            # scripts, all 9, and x: lengths 2+2+2+3+1 = 10 over 5, 4 distinct of 5.
            "code": {
                **counts(missing=2, missing_percent=28.57, distinct=4, distinct_percent=80.0),
                "shortest_length": 1,
                "longest_length": 3,
                "average_length": 2.0,
                "masked_patterns": [
                    pattern("AA", 2),
                    pattern("999", 1),
                    pattern("A", 1),
                    pattern("A9", 1),
                ],
            },
            # A-1 twice, a-1 and b-22: 3 of 4, 3 missing of 7. Lengths 3+3+4+3 = 13 over 4. The
            # hyphen stays, so A-1 and a-1 share A-9.
            "label": {
                **counts(missing=3, missing_percent=42.86, distinct=3, distinct_percent=75.0),
                "shortest_length": 3,
                "longest_length": 4,
                "average_length": 3.25,
                "masked_patterns": [pattern("A-9", 3), pattern("A-99", 1)],
            },
            # `x y`, `x  y` and 7: 3 of 3, 4 missing of 7. Lengths 3+4+1 = 8 over 3, 2.67. Spaces
            # stay as they are, and a space sorts before a letter: 9 < `A  A` < `A A`.
            "free": {
                **counts(missing=4, missing_percent=57.14, distinct=3, distinct_percent=100.0),
                "shortest_length": 1,
                "longest_length": 4,
                "average_length": 2.67,
                "masked_patterns": [pattern("9", 1), pattern("A  A", 1), pattern("A A", 1)],
            },
        },
    },
    # Numbers get counts only. Each distinct count is of PostgreSQL's text for the values, and one
    # NULL in 6 rows is 16.67% missing.
    "numbers": {
        "rows_read": 6,
        "columns": {
            "id": counts(missing=0, missing_percent=0.0, distinct=6, distinct_percent=100.0),
            # 1, 2, 2, -2147483648 and 0: 4 of 5.
            "whole": counts(missing=1, missing_percent=16.67, distinct=4, distinct_percent=80.0),
            # 2**53 twice, 2**53 + 1, the smallest bigint and 0: 4 of 5, as text keeps 2**53 + 1.
            "big": counts(missing=1, missing_percent=16.67, distinct=4, distinct_percent=80.0),
            # 1, 1.0, 1.00, NaN and 0.000: an unscaled numeric keeps the scale it was written with,
            # so they're 5 of 5, although PostgreSQL finds 1, 1.0 and 1.00 equal.
            "exact": counts(missing=1, missing_percent=16.67, distinct=5, distinct_percent=100.0),
            # numeric(5, 2) writes 1, 1.0 as 1.00 and rounds 1.005 to 1.01: 1.00 twice, 1.01, NaN
            # and -999.99, 4 of 5.
            "scaled": counts(missing=1, missing_percent=16.67, distinct=4, distinct_percent=80.0),
            # 0.1 twice, -0, 0, NaN and Infinity. -0 and 0 are equal in PostgreSQL but written
            # apart, so 5 of 6.
            "single": counts(missing=0, missing_percent=0.0, distinct=5, distinct_percent=83.33),
            # 0.3 and 0.1 + 0.2 are two doubles, written 0.3 and 0.30000000000000004; then NaN,
            # Infinity and -Infinity: 5 of 5.
            "double": counts(missing=1, missing_percent=16.67, distinct=5, distinct_percent=100.0),
        },
    },
    # Booleans, JSON and UUIDs get counts only, of PostgreSQL's text for each value.
    "documents": {
        "rows_read": 6,
        "columns": {
            "id": counts(missing=0, missing_percent=0.0, distinct=6, distinct_percent=100.0),
            # yes, on and t are all `true`; off and f are `false`: 2 of 5.
            "flag": counts(missing=1, missing_percent=16.67, distinct=2, distinct_percent=40.0),
            # json keeps what was written, spaces and a repeated key too, so the five are 5 of 5.
            # A JSON null is a value; only row 6's SQL NULL is missing.
            "doc": counts(missing=1, missing_percent=16.67, distinct=5, distinct_percent=100.0),
            # jsonb sorts keys, sets the spaces and keeps a repeated key's last value: rows 1 and 2
            # are {"a": 2, "b": 1}, rows 3 and 6 {"a": 2}, then null and [1, 2.0]: 4 of 6.
            "doc_binary": counts(
                missing=0, missing_percent=0.0, distinct=4, distinct_percent=66.67
            ),
            # Upper case, braces and no hyphens are the same UUID: rows 1 to 3, the nil UUID and
            # row 6's, 3 of 5.
            "ref": counts(missing=1, missing_percent=16.67, distinct=3, distinct_percent=60.0),
        },
    },
    # Arrays and bytea get counts only, an array of text too (the spec's Assumptions).
    "collections": {
        "rows_read": 6,
        "columns": {
            "id": counts(missing=0, missing_percent=0.0, distinct=6, distinct_percent=100.0),
            # {1,2} twice (spacing isn't kept), {}, {NULL}, [0:1]={1,2}, another value as its
            # bounds differ, and {{1,2},{3,4}}: 5 of 6. An empty array and one holding a NULL are
            # values.
            "numbers": counts(missing=0, missing_percent=0.0, distinct=5, distinct_percent=83.33),
            # {a,b} twice (quotes aren't kept), {}, {NULL} and {"a b",c}: 4 of 5.
            "tags": counts(missing=1, missing_percent=16.67, distinct=4, distinct_percent=80.0),
            # abc and \x616263 are the same bytes, \x is the empty value, and hex is the same in
            # either case: 3 of 5.
            "blob": counts(missing=1, missing_percent=16.67, distinct=3, distinct_percent=60.0),
        },
    },
    # Rows 1 to 3 are the same row, and so are 6 and 7, but a fast profile saves no count of
    # copies: the test allows rows_read alone as a metric of the whole object.
    "repeats": {
        "rows_read": 8,
        "columns": {
            # Paris 3 times, Lyon twice and Nice, 3 of 6; 2 missing of 8. Lengths 5+5+5+4+4+4 = 27
            # over 6. Lyon and Nice share a pattern, which sorts before Paris's at the same count.
            "city": {
                **counts(missing=2, missing_percent=25.0, distinct=3, distinct_percent=50.0),
                "shortest_length": 4,
                "longest_length": 5,
                "average_length": 4.5,
                "masked_patterns": [pattern("AAAA", 3), pattern("AAAAA", 3)],
            },
            # 1 three times, 2 and 3: 3 of 5; 3 missing of 8.
            "visits": counts(missing=3, missing_percent=37.5, distinct=3, distinct_percent=60.0),
            # Always missing: no distinct percentage, lengths or patterns, with nothing to measure.
            "note": counts(missing=8, missing_percent=100.0, distinct=0, distinct_percent=None),
        },
    },
    # n is 1 to 100,000 and code is `c` and n. By digits: 9 of 1, 90 of 2, 900 of 3, 9,000 of 4,
    # 90,000 of 5 and 1 of 6, so code's lengths are one more, 2 to 7, and add up to
    # 9×2 + 90×3 + 900×4 + 9,000×5 + 90,000×6 + 1×7 = 588,895, 5.88895 each. That's half-way, and
    # the double nearest it is just above, so 5.89 doesn't show which way a tie would round.
    "many": {
        "rows_read": 100_000,
        "columns": {
            "n": counts(missing=0, missing_percent=0.0, distinct=100_000, distinct_percent=100.0),
            "code": {
                **counts(missing=0, missing_percent=0.0, distinct=100_000, distinct_percent=100.0),
                "shortest_length": 2,
                "longest_length": 7,
                "average_length": 5.89,
                "masked_patterns": [
                    pattern("A99999", 90_000),
                    pattern("A9999", 9_000),
                    pattern("A999", 900),
                    pattern("A99", 90),
                    pattern("A9", 9),
                    pattern("A999999", 1),
                ],
            },
        },
    },
    # Its two partitions' rows: 2023 Ann and Bob, 2024 Ann and a NULL name.
    "visits": {
        "rows_read": 4,
        "columns": {
            "year": counts(missing=0, missing_percent=0.0, distinct=2, distinct_percent=50.0),
            "name": {
                **counts(missing=1, missing_percent=25.0, distinct=2, distinct_percent=66.67),
                "shortest_length": 3,
                "longest_length": 3,
                "average_length": 3.0,
                "masked_patterns": [pattern("AAA", 3)],
            },
        },
    },
    # A row for each name, the NULL one too: Ann seen 2, Bob 1, NULL 1.
    "visit_names": {
        "rows_read": 3,
        "columns": {
            "name": {
                **counts(missing=1, missing_percent=33.33, distinct=2, distinct_percent=100.0),
                "shortest_length": 3,
                "longest_length": 3,
                "average_length": 3.0,
                "masked_patterns": [pattern("AAA", 2)],
            },
            # 2, 1 and 1.
            "seen": counts(missing=0, missing_percent=0.0, distinct=2, distinct_percent=66.67),
        },
    },
    # A view is read from the first rows it returns, here all three that have a name: Ann 2023,
    # Bob 2023, Ann 2024. They're the first rows, not the whole view, so a sample's scope.
    "named_visits": {
        "read": "view_first_rows",
        "rows_read": 3,
        "columns": {
            "name": {
                **counts(missing=0, missing_percent=0.0, distinct=2, distinct_percent=66.67),
                "shortest_length": 3,
                "longest_length": 3,
                "average_length": 3.0,
                "masked_patterns": [pattern("AAA", 3)],
            },
            "year": counts(missing=0, missing_percent=0.0, distinct=2, distinct_percent=66.67),
        },
    },
}
# How an object is read when its entry doesn't say (ADR 0005): each above is small, and analysed
# or never analysed, so whole. Each way gives its metrics' scope, as the spec names them.
READ_WHOLE = "whole"
SCOPE_OF_READ = {"whole": "full", "view_first_rows": "sample"}


@pytest.fixture(scope="module", autouse=True)
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (*DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        for statement in DROP:
            postgres_admin(statement)


def profiled(client: TestClient, name: str) -> dict:
    """A completed fast profile of this one object of the golden schema."""
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    chosen = client.put(
        f"/api/sources/{source_id}/selection", json={"object_ids": [listed[name]["id"]]}
    )
    assert chosen.status_code == 200, chosen.text
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    assert started.status_code == 202, started.text
    deadline = time.monotonic() + 30
    while True:
        run = client.get(f"/api/profile-runs/{started.json()['id']}").json()
        if run["status"] != "running":
            return run
        assert time.monotonic() < deadline
        time.sleep(0.01)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_saved_metric_equals_its_known_value(client: TestClient, name: str) -> None:
    # CFX-APP-postgres/AC-25: compared whole, so an extra or missing metric fails too.
    run = profiled(client, name)

    assert run["status"] == "completed", run["failure"]
    read = EXPECTED[name].get("read", READ_WHOLE)
    assert run["objects"] == [{"object": f"{SCHEMA}.{name}", "method": read}]
    saved: dict[str, dict] = {}
    rows_read = None
    for metric in run["metrics"]:
        # Every metric is exact over the rows read: all of them, or a view's first rows.
        assert (metric["scope"], metric["precision"]) == (SCOPE_OF_READ[read], "exact")
        # Each metric once, so a wrong value saved beside the right one can't hide behind it.
        if metric["column"] is None:
            assert metric["metric"] == "rows_read"
            assert rows_read is None, "rows_read is saved twice"
            rows_read = metric["value"]
        else:
            column = saved.setdefault(metric["column"], {})
            assert metric["metric"] not in column, f"{metric['column']}: {metric['metric']} twice"
            column[metric["metric"]] = metric["value"]
    assert rows_read == EXPECTED[name]["rows_read"]
    assert saved == EXPECTED[name]["columns"]
