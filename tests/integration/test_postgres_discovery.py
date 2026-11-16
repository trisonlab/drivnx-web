"""Listing a database's schemas and objects, CFX-APP-postgres AC-08, AC-09 and AC-10 (DRIVN-49)."""

import traceback
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from connectors.csv_files import duckdb_connection
from connectors.postgres import (
    ConnectFailure,
    ConnectionDetails,
    DeclaredKey,
    DiscoveredColumn,
    DiscoveredObject,
    Discovery,
    attach,
    discover,
)

from .. import postgres_fixture

FIXTURE = ConnectionDetails(
    host=postgres_fixture.HOST,
    port=postgres_fixture.PORT,
    database=postgres_fixture.DATABASE,
    user=postgres_fixture.USER,
    tls_mode="prefer",
)
PASSWORD = postgres_fixture.PASSWORD
SCHEMAS = (
    "drivnx_discovery",
    "drivnx_discovery_ref",
    "drivnx_discovery_empty",
    "drivnx_no_usage",
)
DROP = (
    "DROP SCHEMA IF EXISTS drivnx_discovery, drivnx_discovery_ref, drivnx_discovery_empty,"
    " drivnx_no_usage CASCADE",
    "DROP FOREIGN DATA WRAPPER IF EXISTS drivnx_dummy CASCADE",
)
# Made as the superuser, and read as the fixture's role, which is granted what each case says.
# One statement per line that ends with a semicolon, as the superuser's helper runs one at a time.
OBJECTS = """
CREATE SCHEMA drivnx_discovery_ref;
CREATE TABLE drivnx_discovery_ref.parents (
    region text, code integer, CONSTRAINT parents_key PRIMARY KEY (region, code)
);
CREATE TABLE drivnx_discovery_ref.periods (at date CONSTRAINT periods_key PRIMARY KEY)
    PARTITION BY RANGE (at);
CREATE TABLE drivnx_discovery_ref.periods_2024 PARTITION OF drivnx_discovery_ref.periods
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
INSERT INTO drivnx_discovery_ref.parents VALUES ('north', 1), ('south', 2), ('north', 3);

CREATE SCHEMA drivnx_discovery;
CREATE DOMAIN drivnx_discovery.positive AS integer CHECK (VALUE > 0);
CREATE TABLE drivnx_discovery.orders (
    id bigint CONSTRAINT orders_key PRIMARY KEY,
    region text NOT NULL,
    code integer NOT NULL,
    amount numeric(10,2),
    CONSTRAINT orders_code_region UNIQUE (code, region),
    CONSTRAINT orders_parent FOREIGN KEY (region, code)
        REFERENCES drivnx_discovery_ref.parents (region, code)
);
INSERT INTO drivnx_discovery.orders VALUES
    (1, 'north', 1, 9.50), (2, 'south', 2, 3.25), (3, 'north', 3, 1.00);
CREATE TABLE drivnx_discovery.events (id integer, at date) PARTITION BY RANGE (at);
CREATE TABLE drivnx_discovery.events_2024 PARTITION OF drivnx_discovery.events
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE drivnx_discovery.events_2025 PARTITION OF drivnx_discovery.events
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE drivnx_discovery.visits (id integer, at date) PARTITION BY RANGE (at);
CREATE TABLE drivnx_discovery.visits_2024 PARTITION OF drivnx_discovery.visits
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE drivnx_discovery.visits_2025 PARTITION OF drivnx_discovery.visits
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE drivnx_discovery.visits_2026 PARTITION OF drivnx_discovery.visits
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01') PARTITION BY RANGE (at);
CREATE TABLE drivnx_discovery.visits_2026_h1 PARTITION OF drivnx_discovery.visits_2026
    FOR VALUES FROM ('2026-01-01') TO ('2026-07-01');
INSERT INTO drivnx_discovery.visits VALUES
    (1, '2024-03-01'), (2, '2024-04-01'), (3, '2024-05-01'), (4, '2025-03-01'),
    (5, '2025-04-01'), (6, '2026-03-01');
ANALYZE drivnx_discovery.visits_2024;
ANALYZE drivnx_discovery.visits_2025;
CREATE VIEW drivnx_discovery.order_view AS SELECT id, amount FROM drivnx_discovery.orders;
CREATE MATERIALIZED VIEW drivnx_discovery.order_totals AS
    SELECT region, count(*) AS orders FROM drivnx_discovery.orders GROUP BY region;
ANALYZE drivnx_discovery.order_totals;
CREATE MATERIALIZED VIEW drivnx_discovery.order_regions AS
    SELECT DISTINCT region FROM drivnx_discovery.orders WITH NO DATA;
CREATE FOREIGN DATA WRAPPER drivnx_dummy;
CREATE SERVER drivnx_dummy_server FOREIGN DATA WRAPPER drivnx_dummy;
CREATE FOREIGN TABLE drivnx_discovery.remote (id integer) SERVER drivnx_dummy_server;
CREATE TABLE drivnx_discovery.bookings (
    at date CONSTRAINT bookings_period REFERENCES drivnx_discovery_ref.periods
);
CREATE TABLE drivnx_discovery.typed (
    small smallint NOT NULL, normal integer, big bigint, single real, double double precision,
    exact numeric(10,2), flag boolean, day date, clock time, zoned_clock timetz,
    moment timestamp, zoned_moment timestamptz, words text, id uuid, document json,
    positive_count drivnx_discovery.positive NOT NULL, labels text[]
);
CREATE TABLE drivnx_discovery.secret (id integer CONSTRAINT secret_key PRIMARY KEY, token text);
CREATE TABLE drivnx_discovery."Odd.Name, é" (
    "a,b" integer, "c""d" text, CONSTRAINT "key, odd" UNIQUE ("c""d", "a,b")
);

CREATE SCHEMA drivnx_no_usage;
CREATE TABLE drivnx_no_usage.hidden (id integer CONSTRAINT hidden_key PRIMARY KEY);

CREATE SCHEMA drivnx_discovery_empty;
CREATE TEMPORARY TABLE drivnx_discovery_temporary (id integer);

GRANT USAGE ON SCHEMA drivnx_discovery, drivnx_discovery_ref, drivnx_discovery_empty
    TO drivnx_fixture;
GRANT SELECT ON drivnx_discovery_ref.parents, drivnx_discovery.orders, drivnx_discovery.events,
    drivnx_discovery.order_view, drivnx_discovery.order_totals, drivnx_discovery.order_regions,
    drivnx_discovery."Odd.Name, é",
    drivnx_no_usage.hidden
    TO drivnx_fixture;
"""


@pytest.fixture(scope="module")
def discovery(
    postgres_admin: Callable[[str], None], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Discovery]:
    """One discovery of the objects above, as the fixture's role sees them."""
    for statement in (*DROP, *OBJECTS.strip().rstrip(";").split(";\n")):
        postgres_admin(statement)
    try:
        yield discover(FIXTURE, PASSWORD, tmp_path_factory.mktemp("discovery"))
    finally:
        for statement in DROP:
            postgres_admin(statement)


def objects_in(discovery: Discovery, schema: str) -> dict[str, DiscoveredObject]:
    return {found.name: found for found in discovery.objects if found.schema == schema}


def test_every_kind_of_object_is_listed_with_its_kind_and_estimate(discovery: Discovery) -> None:
    # CFX-APP-postgres/AC-08: tables, partitioned tables, views and materialized views, each with
    # its kind and estimated row count. Only the materialized view has been analysed: two regions.
    listed = {
        (found.name, found.kind, found.estimated_rows)
        for found in objects_in(discovery, "drivnx_discovery").values()
    }

    assert listed >= {
        ("orders", "table", None),
        ("events", "partitioned_table", None),
        ("order_view", "view", None),
        ("order_totals", "materialized_view", 2),
    }


def test_a_partitioned_tables_estimate_is_its_analysed_partitions_rows(
    discovery: Discovery,
) -> None:
    # CFX-APP-postgres/AC-08: autovacuum analyses partitions and never their parent, whose own
    # estimate then stays at -1. Three rows in 2024 and two in 2025 are analysed; 2026's one row is
    # in a partition never analysed, so it is left out.
    assert objects_in(discovery, "drivnx_discovery")["visits"].estimated_rows == 5


def test_partitions_and_foreign_tables_are_not_listed(discovery: Discovery) -> None:
    # CFX-APP-postgres/AC-08: partitions hidden, under their partitioned table; foreign tables
    # aren't listed.
    listed = objects_in(discovery, "drivnx_discovery")

    assert "events" in listed
    assert not {"events_2024", "events_2025", "remote"} & listed.keys()


def test_system_and_temporary_schemas_are_not_listed(discovery: Discovery) -> None:
    # CFX-APP-postgres/AC-08: every schema the user can see, an empty one too, except the system
    # schemas and the temporary ones (pg_temp_N, pg_toast_temp_N), which the fixture made by
    # creating a temporary table.
    shown = set(discovery.schemas) | {found.schema for found in discovery.objects}

    assert set(discovery.schemas) >= {"public", *SCHEMAS}
    assert "information_schema" not in shown
    assert not [schema for schema in shown if schema.startswith("pg_")]


def test_an_object_without_select_or_without_schema_usage_is_listed_as_not_readable(
    discovery: Discovery,
) -> None:
    # CFX-APP-postgres/AC-10: listed, as "no read access". The fixture's role can SELECT from
    # drivnx_no_usage.hidden but not use its schema, so can't read it either.
    listed = objects_in(discovery, "drivnx_discovery")
    hidden = objects_in(discovery, "drivnx_no_usage")["hidden"]

    assert listed["orders"].readable is True
    assert listed["order_view"].readable is True
    assert listed["secret"].readable is False
    assert hidden.readable is False


def test_a_materialized_view_never_refreshed_is_listed_as_not_populated(
    discovery: Discovery,
) -> None:
    # CFX-APP-postgres/AC-10, as agreed for DRIVN-49: the role may SELECT from it, but every read
    # fails with "has not been populated", so it is listed and can't be chosen.
    listed = objects_in(discovery, "drivnx_discovery")

    assert listed["order_regions"].readable is True
    assert listed["order_regions"].populated is False
    assert listed["order_totals"].populated is True
    assert listed["orders"].populated is True


def test_an_objects_columns_have_their_types_normalised_types_and_whether_they_allow_nulls(
    discovery: Discovery,
) -> None:
    # CFX-APP-postgres/AC-09: in the table's order. The normalised types are ADR 0003's
    # vocabulary; a domain has its base type's, and anything else is a string.
    typed = objects_in(discovery, "drivnx_discovery")["typed"]

    assert typed.columns == (
        DiscoveredColumn("small", "smallint", "integer", allows_nulls=False),
        DiscoveredColumn("normal", "integer", "integer", allows_nulls=True),
        DiscoveredColumn("big", "bigint", "integer", allows_nulls=True),
        DiscoveredColumn("single", "real", "number", allows_nulls=True),
        DiscoveredColumn("double", "double precision", "number", allows_nulls=True),
        DiscoveredColumn("exact", "numeric(10,2)", "number", allows_nulls=True),
        DiscoveredColumn("flag", "boolean", "boolean", allows_nulls=True),
        DiscoveredColumn("day", "date", "date", allows_nulls=True),
        DiscoveredColumn("clock", "time without time zone", "time", allows_nulls=True),
        DiscoveredColumn("zoned_clock", "time with time zone", "time", allows_nulls=True),
        DiscoveredColumn("moment", "timestamp without time zone", "timestamp", allows_nulls=True),
        DiscoveredColumn(
            "zoned_moment", "timestamp with time zone", "timestamp", allows_nulls=True
        ),
        DiscoveredColumn("words", "text", "string", allows_nulls=True),
        DiscoveredColumn("id", "uuid", "string", allows_nulls=True),
        DiscoveredColumn("document", "json", "string", allows_nulls=True),
        DiscoveredColumn(
            "positive_count", "drivnx_discovery.positive", "integer", allows_nulls=False
        ),
        DiscoveredColumn("labels", "text[]", "string", allows_nulls=True),
    )


def test_a_views_columns_are_listed_too(discovery: Discovery) -> None:
    # CFX-APP-postgres/AC-09: any listed object can be opened.
    view = objects_in(discovery, "drivnx_discovery")["order_view"]

    assert [(column.name, column.physical_type) for column in view.columns] == [
        ("id", "bigint"),
        ("amount", "numeric(10,2)"),
    ]


def test_declared_primary_unique_and_foreign_keys_are_found_with_what_they_reference(
    discovery: Discovery,
) -> None:
    # CFX-APP-postgres/AC-09: each key's columns in the key's order, not the table's.
    orders = objects_in(discovery, "drivnx_discovery")["orders"]

    assert set(orders.keys) == {
        DeclaredKey("primary_key", "orders_key", ("id",)),
        DeclaredKey("unique", "orders_code_region", ("code", "region")),
        DeclaredKey(
            "foreign_key",
            "orders_parent",
            ("region", "code"),
            referenced_schema="drivnx_discovery_ref",
            referenced_object="parents",
            referenced_columns=("region", "code"),
        ),
    }


def test_a_keys_column_names_are_kept_whole_whatever_they_hold(discovery: Discovery) -> None:
    # CFX-APP-postgres/AC-09: names with a comma, a quote, a dot, a space and an accent.
    odd = objects_in(discovery, "drivnx_discovery")["Odd.Name, é"]

    assert odd.keys == (DeclaredKey("unique", "key, odd", ('c"d', "a,b")),)


def test_a_foreign_key_to_a_partitioned_table_is_one_key_to_that_table(
    discovery: Discovery,
) -> None:
    # CFX-APP-postgres/AC-09: PostgreSQL keeps a copy of the key for each partition referenced,
    # and partitions are hidden.
    bookings = objects_in(discovery, "drivnx_discovery")["bookings"]
    periods = objects_in(discovery, "drivnx_discovery_ref")["periods"]

    assert bookings.keys == (
        DeclaredKey(
            "foreign_key",
            "bookings_period",
            ("at",),
            referenced_schema="drivnx_discovery_ref",
            referenced_object="periods",
            referenced_columns=("at",),
        ),
    )
    assert periods.keys == (DeclaredKey("primary_key", "periods_key", ("at",)),)


def test_discovery_reads_no_row(discovery: Discovery) -> None:
    # CFX-APP-postgres/AC-09: "without reading any row". The fixture's role can't read a row of
    # either of these, yet discovery found their columns and keys.
    secret = objects_in(discovery, "drivnx_discovery")["secret"]
    hidden = objects_in(discovery, "drivnx_no_usage")["hidden"]

    assert [column.name for column in secret.columns] == ["id", "token"]
    assert secret.keys == (DeclaredKey("primary_key", "secret_key", ("id",)),)
    assert [column.name for column in hidden.columns] == ["id"]
    assert hidden.keys == (DeclaredKey("primary_key", "hidden_key", ("id",)),)


def test_a_catalog_query_that_fails_says_so_without_duckdbs_text(
    postgres_admin: Callable[[str], None], tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-08: the server refuses one of the catalog queries. DuckDB's text, which
    # repeats the query and the server's error, is never shown.
    function = "FUNCTION pg_catalog.has_schema_privilege(oid, text)"
    postgres_admin(f"REVOKE EXECUTE ON {function} FROM PUBLIC")
    try:
        with pytest.raises(ConnectFailure) as failed:
            discover(FIXTURE, PASSWORD, tmp_path)
    finally:
        postgres_admin(f"GRANT EXECUTE ON {function} TO PUBLIC")

    assert str(failed.value) == (
        "DrivnX couldn't list this database's schemas and tables. Try again."
    )
    assert "permission denied" not in "".join(traceback.format_exception(failed.value))


def test_discovery_has_a_30_second_statement_timeout(postgres_server: str, tmp_path: Path) -> None:
    # CFX-APP-postgres, the assumption that every query DrivnX sends has a statement_timeout, 30 s
    # for discovery (AC-08, AC-09): set for the whole connection, under DuckDB's own queries too.
    with duckdb_connection(tmp_path) as connection:
        attach(connection, FIXTURE, PASSWORD)
        (timeout,) = connection.execute(
            "SELECT * FROM postgres_query('source',"
            " 'SELECT current_setting(''statement_timeout'')')"
        ).fetchone()

    assert timeout == "30s"
