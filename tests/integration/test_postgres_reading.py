"""How a fast profile reads each chosen object, CFX-APP-postgres AC-18 and ADR 0005 (DRIVN-51)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import duckdb
import pytest
from connectors.csv_files import duckdb_connection
from connectors.postgres import (
    ConnectionDetails,
    Reading,
    attach,
    load_object_rows,
    reading_for,
)

from .. import postgres_fixture

FIXTURE = ConnectionDetails(
    host=postgres_fixture.HOST,
    port=postgres_fixture.PORT,
    database=postgres_fixture.DATABASE,
    user=postgres_fixture.USER,
    tls_mode="prefer",
)
SCHEMA = "drivnx_reading"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser. Autovacuum is off on each table, so its estimate stays what the test set:
# `misestimated` was analysed empty, then filled past the guard.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.big (id integer, label text) WITH (autovacuum_enabled = false)",
    f"INSERT INTO {SCHEMA}.big SELECT n, 'row ' || n FROM generate_series(1, 150000) AS n",
    f"ANALYZE {SCHEMA}.big",
    f"CREATE TABLE {SCHEMA}.small (id integer, label text) WITH (autovacuum_enabled = false)",
    f"INSERT INTO {SCHEMA}.small VALUES (1, 'one'), (2, NULL), (3, '')",
    f"ANALYZE {SCHEMA}.small",
    f"CREATE TABLE {SCHEMA}.misestimated (id integer) WITH (autovacuum_enabled = false)",
    f"ANALYZE {SCHEMA}.misestimated",
    f"INSERT INTO {SCHEMA}.misestimated SELECT n FROM generate_series(1, 100050) AS n",
    f"CREATE VIEW {SCHEMA}.big_view AS SELECT id, label FROM {SCHEMA}.big",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO drivnx_fixture",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO drivnx_fixture",
)


@pytest.fixture(scope="module", autouse=True)
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (*DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        for statement in DROP:
            postgres_admin(statement)


@pytest.fixture
def attached(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A DuckDB connection with the fixture attached, as a profile job holds one."""
    with duckdb_connection(tmp_path) as connection:
        attach(connection, FIXTURE, postgres_fixture.PASSWORD)
        yield connection


def count(connection: duckdb.DuckDBPyConnection, table: str) -> int:
    return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def same_rows(connection: duckdb.DuckDBPyConnection, one: str, other: str) -> bool:
    return connection.execute(
        f"SELECT count(*) FROM ((SELECT * FROM {one} EXCEPT ALL SELECT * FROM {other})"
        f" UNION ALL (SELECT * FROM {other} EXCEPT ALL SELECT * FROM {one}))"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("kind", "estimated_rows", "expected"),
    [
        ("view", None, Reading("view_first_rows")),
        ("view", 5_000_000, Reading("view_first_rows")),
        # 100,000 ÷ 150,000 of the pages, as a percentage, reads about 100,000 rows.
        ("table", 150_000, Reading("page_sample", pytest.approx(200 / 3))),
        ("partitioned_table", 150_000, Reading("page_sample", pytest.approx(200 / 3))),
        ("materialized_view", 1_000_000, Reading("page_sample", pytest.approx(10.0))),
        ("table", 100_000, Reading("whole")),
        ("table", 0, Reading("whole")),
        # Never analysed: no estimate, so read whole, with the guard.
        ("table", None, Reading("whole")),
    ],
)
def test_each_kind_of_object_is_read_as_the_spec_says(
    kind: str, estimated_rows: int | None, expected: Reading
) -> None:
    # CFX-APP-postgres/AC-18
    assert reading_for(kind, estimated_rows) == expected


def test_a_page_sample_reads_about_100000_rows_and_the_same_seed_reads_them_again(
    attached: duckdb.DuckDBPyConnection,
) -> None:
    # CFX-APP-postgres/AC-18 and ADR 0005: about 100,000 rows from across the table, picked by
    # page, which the seed saved in the run picks again; another seed picks others.
    reading = reading_for("table", 150_000)
    big = (SCHEMA, "big")

    method = load_object_rows(attached, big, ("id", "label"), reading, 42, "first")
    load_object_rows(attached, big, ("id", "label"), reading, 42, "again")
    load_object_rows(attached, big, ("id", "label"), reading, 7, "other")

    assert method == "page_sample"
    assert 80_000 <= count(attached, "first") <= 120_000
    assert same_rows(attached, "first", "again")
    assert not same_rows(attached, "first", "other")


def test_a_small_table_is_read_whole_with_null_apart_from_empty_text(
    attached: duckdb.DuckDBPyConnection,
) -> None:
    # CFX-APP-postgres/AC-18: estimated at 100,000 rows or fewer, so all of it; each value as
    # PostgreSQL's text, and NULL still NULL (the rule agreed for DRIVN-51).
    method = load_object_rows(
        attached, (SCHEMA, "small"), ("id", "label"), reading_for("table", 3), 42, "rows"
    )

    assert method == "whole"
    assert attached.execute("SELECT c0, c1 FROM rows ORDER BY c0").fetchall() == [
        ("1", "one"),
        ("2", None),
        ("3", ""),
    ]


def test_a_table_larger_than_its_estimate_is_its_first_rows(
    attached: duckdb.DuckDBPyConnection,
) -> None:
    # CFX-APP-postgres/AC-18: estimated small, but more than 100,000 rows come back, so the
    # results are the first rows returned, not random; one row past them says so.
    method = load_object_rows(
        attached, (SCHEMA, "misestimated"), ("id",), reading_for("table", 0), 42, "rows"
    )

    assert method == "first_rows"
    assert count(attached, "rows") == 100_001


def test_a_view_is_read_from_its_first_100000_rows(attached: duckdb.DuckDBPyConnection) -> None:
    # CFX-APP-postgres/AC-18: PostgreSQL can't sample a view by page.
    method = load_object_rows(
        attached, (SCHEMA, "big_view"), ("id", "label"), reading_for("view", None), 42, "rows"
    )

    assert method == "view_first_rows"
    assert count(attached, "rows") == 100_000
