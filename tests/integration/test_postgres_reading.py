"""How a fast profile reads each chosen object, DRIVN-45 AC-18 and ADR 0005 (DRIVN-51)."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest
from connectors.csv_files import duckdb_connection
from connectors.postgres import (
    ConnectFailure,
    ConnectionDetails,
    Reading,
    attach,
    load_object_rows,
    load_whole_object,
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
    # The types whose text the scanner writes differently from PostgreSQL unless told (ADR 0009),
    # and the ones that depend on the server's time zone and locale. A row of NULLs too.
    f"""CREATE TABLE {SCHEMA}.typed (
        unscaled numeric, scaled numeric(10, 2), wide numeric(50, 10), integers integer[],
        grid integer[][], words text[], bytes bytea, document json, binary_document jsonb,
        moment timestamptz, clock timetz, span interval, amount money
    )""",
    f"""INSERT INTO {SCHEMA}.typed VALUES
        (1.00, 1.5, 12345678901234567890123456789.0123456789, '{{1,2}}', '{{{{1,2}},{{3,4}}}}',
         '{{"x y",z,NULL,"a,b"}}', '\\xdeadbeef', '{{"a": 1}}', '{{"a": 1}}',
         '2024-03-25 10:00:00.123456+02', '10:00+02', '1 day 02:00', 12.5),
        (NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)""",
    # Two columns whose names differ only in case, which DuckDB's own names can't tell apart.
    f'CREATE TABLE {SCHEMA}.two_cases ("A" text, a text)',
    f"INSERT INTO {SCHEMA}.two_cases VALUES ('upper', 'lower')",
    # PostgreSQL allows a table with no columns; its rows are still rows.
    f"CREATE TABLE {SCHEMA}.no_columns ()",
    f"INSERT INTO {SCHEMA}.no_columns DEFAULT VALUES",
    f"INSERT INTO {SCHEMA}.no_columns DEFAULT VALUES",
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


# A role of its own whose sessions are in a time zone a test sets, as a server's or a role's
# `timezone` would set it, so the fixture's role keeps the server's.
ZONED_USER = "drivnx_zoned"
ZONED_PASSWORD = "drivnx-zoned"  # pragma: allowlist secret, ai-skills: allow
# A zone every PostgreSQL takes, a POSIX one it parses itself, 3 hours east, and DuckDB refuses.
# Not `localtime`, which only a PostgreSQL using the system's zone files takes: Debian's, not
# Homebrew's or the Windows runner's.
REFUSED_ZONE = "<+03>-03"


@pytest.fixture(scope="module")
def zoned_role(
    objects: None, postgres_admin: Callable[[str], None]
) -> Iterator[Callable[[str], None]]:
    """Set the time zone the zoned role's next sessions are in."""
    for statement in (
        f"DROP ROLE IF EXISTS {ZONED_USER}",
        f"CREATE ROLE {ZONED_USER} LOGIN PASSWORD '{ZONED_PASSWORD}'",
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO {ZONED_USER}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {ZONED_USER}",
    ):
        postgres_admin(statement)
    try:
        yield lambda zone: postgres_admin(f"ALTER ROLE {ZONED_USER} SET timezone = '{zone}'")
    finally:
        postgres_admin(f"DROP OWNED BY {ZONED_USER}")
        postgres_admin(f"DROP ROLE {ZONED_USER}")


@pytest.fixture
def attached_in_zone(
    tmp_path: Path, zoned_role: Callable[[str], None]
) -> Iterator[Callable[[str], duckdb.DuckDBPyConnection]]:
    """A connection attached as the zoned role, its sessions in the zone given."""
    with duckdb_connection(tmp_path) as connection:

        def attached(zone: str) -> duckdb.DuckDBPyConnection:
            zoned_role(zone)
            attach(connection, replace(FIXTURE, user=ZONED_USER), ZONED_PASSWORD)
            return connection

        yield attached


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
    # DRIVN-45/AC-18
    assert reading_for(kind, estimated_rows) == expected


def test_a_page_sample_reads_about_100000_rows_and_the_same_seed_reads_them_again(
    attached: duckdb.DuckDBPyConnection,
) -> None:
    # DRIVN-45/AC-18 and ADR 0005: about 100,000 rows from across the table, picked by
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
    # DRIVN-45/AC-18: estimated at 100,000 rows or fewer, so all of it; each value as
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
    # DRIVN-45/AC-18: estimated small, but more than 100,000 rows come back, so the
    # results are the first rows returned, not random; one row past them says so.
    method = load_object_rows(
        attached, (SCHEMA, "misestimated"), ("id",), reading_for("table", 0), 42, "rows"
    )

    assert method == "first_rows"
    assert count(attached, "rows") == 100_001


def test_a_view_is_read_from_its_first_100000_rows(attached: duckdb.DuckDBPyConnection) -> None:
    # DRIVN-45/AC-18: PostgreSQL can't sample a view by page.
    method = load_object_rows(
        attached, (SCHEMA, "big_view"), ("id", "label"), reading_for("view", None), 42, "rows"
    )

    assert method == "view_first_rows"
    assert count(attached, "rows") == 100_000


TYPED_COLUMNS = (
    "unscaled", "scaled", "wide", "integers", "grid", "words", "bytes", "document",
    "binary_document", "moment", "clock", "span", "amount",
)


# DrivnX's own time zone, which DuckDB takes from the machine's: UTC, as CI's runners, or not.
@pytest.mark.parametrize("zone", ["UTC", "Asia/Tokyo"])
def test_a_whole_read_gives_each_value_as_postgresqls_text(
    attached: duckdb.DuckDBPyConnection, zone: str
) -> None:
    # DRIVN-45/AC-19 and ADR 0009: a full profile measures the same text a fast profile
    # does, so `1.00` stays `1.00`, an array `{1,2}` and `bytea` `\xdeadbeef`, as the server
    # writes them, and a `timestamptz` in the server's time zone, wherever DrivnX runs (found in
    # review). The expected text is the server's own `::text`.
    attached.execute(f"SET TimeZone = '{zone}'")
    as_text = ", ".join(f'"{column}"::text AS c{n}' for n, column in enumerate(TYPED_COLUMNS))
    attached.execute(
        "CREATE TABLE expected AS SELECT * FROM postgres_query('source', "
        f"'SELECT {as_text} FROM {SCHEMA}.typed')"
    )

    load_whole_object(attached, (SCHEMA, "typed"), TYPED_COLUMNS, "rows")

    assert count(attached, "rows") == 2
    assert attached.execute("SELECT * FROM rows EXCEPT ALL SELECT * FROM expected").fetchall() == []
    assert same_rows(attached, "rows", "expected")


def test_a_whole_read_keeps_columns_whose_names_differ_only_in_case_apart(
    attached: duckdb.DuckDBPyConnection,
) -> None:
    # DRIVN-45/AC-19: "exact results" for each column, `"A"` and `a` being two
    # (found in review). PostgreSQL's quoted names are case-sensitive; DuckDB's aren't.
    load_whole_object(attached, (SCHEMA, "two_cases"), ("A", "a"), "rows")

    assert attached.execute("SELECT * FROM rows").fetchall() == [("upper", "lower")]


def test_a_whole_read_counts_the_rows_of_a_table_with_no_columns(
    attached: duckdb.DuckDBPyConnection,
) -> None:
    # DRIVN-45/AC-19: "each object's exact row count", for a table with no columns too,
    # as a fast reading counts them (found in review).
    load_whole_object(attached, (SCHEMA, "no_columns"), (), "rows")

    assert count(attached, "rows") == 2


def test_a_whole_read_without_zoned_times_ignores_a_time_zone_duckdb_cant_read(
    attached_in_zone: Callable[[str], duckdb.DuckDBPyConnection],
) -> None:
    # DRIVN-45/AC-19: an object with no `timestamptz` is read whole whatever the server's
    # time zone. Every PostgreSQL takes a POSIX zone with a quoted name, and DuckDB refuses it
    # (found in the second review).
    connection = attached_in_zone(REFUSED_ZONE)

    load_whole_object(connection, (SCHEMA, "small"), ("id", "label"), "rows")

    assert count(connection, "rows") == 3


# The zones PostgreSQL takes and DuckDB can't write its times in: one it refuses, and a POSIX one
# whose sign it reads the other way, so `UTC+3` would write -03:00's times as +03:00 (found in the
# second review).
@pytest.mark.parametrize("zone", [REFUSED_ZONE, "UTC+3"])
def test_a_whole_read_of_zoned_times_in_a_zone_duckdb_cant_write_says_so(
    attached_in_zone: Callable[[str], duckdb.DuckDBPyConnection], zone: str
) -> None:
    # DRIVN-45/AC-19: exact results or none. No other text for a `timestamptz` than the
    # server's, and a reason the user can act on (decided with the user for DRIVN-53).
    connection = attached_in_zone(zone)

    with pytest.raises(ConnectFailure) as failed:
        load_whole_object(connection, (SCHEMA, "typed"), TYPED_COLUMNS, "rows")

    assert (failed.value.reason, str(failed.value)) == (
        "time_zone",
        f"DrivnX can't read times in this server's time zone, {zone}. Set the connection's user to"
        " a named zone such as UTC, or leave this object out.",
    )


def test_a_whole_read_of_zoned_times_writes_them_in_the_servers_named_zone(
    attached_in_zone: Callable[[str], duckdb.DuckDBPyConnection],
) -> None:
    # DRIVN-45/AC-19 and ADR 0009: in a server zone DuckDB can write, other than UTC and
    # this machine's, a `timestamptz` reads whole as the server's own `::text`.
    connection = attached_in_zone("Asia/Kolkata")
    connection.execute(
        "CREATE TABLE expected AS SELECT * FROM postgres_query('source', "
        f"'SELECT moment::text AS c0, clock::text AS c1 FROM {SCHEMA}.typed')"
    )

    load_whole_object(connection, (SCHEMA, "typed"), TYPED_COLUMNS, "rows")

    # `moment` and `clock`, the 10th and 11th of `TYPED_COLUMNS`.
    read = "SELECT c9, c10 FROM rows"
    assert connection.execute(f"{read} EXCEPT ALL SELECT * FROM expected").fetchall() == []
    assert connection.execute(f"{read} WHERE c9 LIKE '%+05:30'").fetchall() != []


@pytest.mark.parametrize(
    "listed",
    [
        # `label` renamed `name` since the object was listed.
        ("id", "name"),
        # `label` dropped and `name` added, so as many columns as listed.
        ("id", "label", "name"),
        # The same names in another order, as dropping and adding them would leave them.
        ("label", "id"),
    ],
)
def test_a_whole_read_of_an_object_whose_columns_changed_since_it_was_listed_says_so(
    attached: duckdb.DuckDBPyConnection, listed: tuple[str, ...]
) -> None:
    # DRIVN-45/AC-19: each column's results are its own. Read by position, a column the
    # server has under another name or place would be measured as the listed one (found in the
    # second review).
    with pytest.raises(ConnectFailure) as failed:
        load_whole_object(attached, (SCHEMA, "small"), listed, "rows")

    assert failed.value.reason == "changed"
