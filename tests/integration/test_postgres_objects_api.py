"""Listing, opening, choosing and refreshing a database's objects through the API,
CFX-APP-postgres AC-08, AC-09, AC-10, AC-11 and AC-13 (DRIVN-49)."""

import socket
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from .. import postgres_fixture
from ..conftest import StartDrivnX
from .test_session import sign_in

DETAILS = {
    "host": postgres_fixture.HOST,
    "port": postgres_fixture.PORT,
    "database": postgres_fixture.DATABASE,
    "user": postgres_fixture.USER,
    "password": postgres_fixture.PASSWORD,
    "tls_mode": "prefer",
}
SCHEMA = "drivnx_api_objects"
DROP = (
    f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",
    "DROP FOREIGN DATA WRAPPER IF EXISTS drivnx_api_dummy CASCADE",
)
# Made as the superuser, and read as the fixture's role. One statement per line that ends with a
# semicolon, as the superuser's helper runs one at a time.
OBJECTS = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.customers (id integer CONSTRAINT customers_key PRIMARY KEY, name text);
CREATE TABLE {SCHEMA}.orders (
    id bigint CONSTRAINT orders_key PRIMARY KEY,
    customer_id integer NOT NULL CONSTRAINT orders_customer REFERENCES {SCHEMA}.customers,
    amount numeric(10,2)
);
CREATE TABLE {SCHEMA}.events (id integer, at date) PARTITION BY RANGE (at);
CREATE TABLE {SCHEMA}.events_2024 PARTITION OF {SCHEMA}.events
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE VIEW {SCHEMA}.customer_names AS SELECT name FROM {SCHEMA}.customers;
CREATE MATERIALIZED VIEW {SCHEMA}.order_count AS SELECT count(*) AS orders FROM {SCHEMA}.orders;
ANALYZE {SCHEMA}.order_count;
CREATE MATERIALIZED VIEW {SCHEMA}.customer_count AS SELECT count(*) AS customers
    FROM {SCHEMA}.customers WITH NO DATA;
CREATE FOREIGN DATA WRAPPER drivnx_api_dummy;
CREATE SERVER drivnx_api_dummy_server FOREIGN DATA WRAPPER drivnx_api_dummy;
CREATE FOREIGN TABLE {SCHEMA}.remote (id integer) SERVER drivnx_api_dummy_server;
CREATE TABLE {SCHEMA}.secret (id integer, token text);
GRANT USAGE ON SCHEMA {SCHEMA} TO drivnx_fixture;
GRANT SELECT ON {SCHEMA}.customers, {SCHEMA}.orders, {SCHEMA}.events, {SCHEMA}.customer_names,
    {SCHEMA}.order_count, {SCHEMA}.customer_count TO drivnx_fixture;
"""


@pytest.fixture(scope="module", autouse=True)
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    """The objects above, in the fixture's database, for every test here."""
    for statement in (*DROP, *OBJECTS.strip().rstrip(";").split(";\n")):
        postgres_admin(statement)
    try:
        yield
    finally:
        for statement in DROP:
            postgres_admin(statement)


def objects_in(listed: dict, schema: str = SCHEMA) -> dict[str, dict]:
    (found,) = [each for each in listed["schemas"] if each["name"] == schema]
    return {each["name"]: each for each in found["objects"]}


def test_saving_a_new_connection_lists_its_objects(client: TestClient) -> None:
    # CFX-APP-postgres/AC-08: a connected source lists its schemas and their objects, without the
    # user asking.
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]

    response = client.get(f"/api/sources/{source_id}/objects")

    assert response.status_code == 200, response.text
    listed = response.json()
    assert listed["discovered_at"]
    assert {"customers", "orders"} <= objects_in(listed).keys()


def test_a_new_connection_is_saved_even_when_listing_its_objects_fails(
    client: TestClient, postgres_admin: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-08: the connection works, so it is saved; the server refuses one of the
    # catalog queries, so nothing is listed yet, and the screen offers to list them.
    function = "FUNCTION pg_catalog.has_schema_privilege(oid, text)"
    postgres_admin(f"REVOKE EXECUTE ON {function} FROM PUBLIC")
    try:
        response = client.post("/api/sources/postgres", json=DETAILS)
    finally:
        postgres_admin(f"GRANT EXECUTE ON {function} TO PUBLIC")

    assert response.status_code == 201, response.text
    assert client.get("/api/sources").json() == [response.json()]
    listed = client.get(f"/api/sources/{response.json()['id']}/objects").json()
    assert listed == {"discovered_at": None, "schemas": []}


def test_refreshing_lists_every_kind_and_leaves_out_partitions_and_foreign_tables(
    client: TestClient,
) -> None:
    # CFX-APP-postgres/AC-08: each object with its kind and estimate; partitions are under their
    # partitioned table, foreign tables aren't listed. Only the materialized view, of one row, has
    # been analysed.
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    before = client.get(f"/api/sources/{source_id}/objects").json()["discovered_at"]

    response = client.post(f"/api/sources/{source_id}/discover")

    assert response.status_code == 200, response.text
    listed = response.json()
    assert listed["discovered_at"] > before
    assert {
        name: (found["kind"], found["estimated_rows"])
        for name, found in objects_in(listed).items()
    } == {
        "customers": ("table", None),
        "customer_count": ("materialized_view", None),
        "customer_names": ("view", None),
        "events": ("partitioned_table", None),
        "order_count": ("materialized_view", 1),
        "orders": ("table", None),
        "secret": ("table", None),
    }
    assert client.get(f"/api/sources/{source_id}/objects").json() == listed


def saved_source(client: TestClient) -> tuple[str, dict[str, dict]]:
    """A saved connection to the fixture, and its objects in this test's schema by name."""
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    return source_id, objects_in(client.get(f"/api/sources/{source_id}/objects").json())


def test_opening_an_object_shows_its_columns_and_keys(client: TestClient) -> None:
    # CFX-APP-postgres/AC-09: each column's name, PostgreSQL type, normalised type and whether it
    # allows nulls, and the declared keys with what a foreign key references.
    source_id, listed = saved_source(client)
    orders = listed["orders"]

    response = client.get(f"/api/sources/{source_id}/objects/{orders['id']}")

    assert response.status_code == 200, response.text
    opened = response.json()
    # In no particular order: the server lists keys as it likes.
    opened["keys"].sort(key=lambda key: key["kind"])
    assert opened == {
        **orders,
        "schema": SCHEMA,
        "columns": [
            {"name": "id", "physical_type": "bigint", "normalised_type": "integer",
             "allows_nulls": False},
            {"name": "customer_id", "physical_type": "integer", "normalised_type": "integer",
             "allows_nulls": False},
            {"name": "amount", "physical_type": "numeric(10,2)", "normalised_type": "number",
             "allows_nulls": True},
        ],
        "keys": [
            {
                "kind": "foreign_key",
                "name": "orders_customer",
                "columns": ["customer_id"],
                "references": {"schema": SCHEMA, "object": "customers", "columns": ["id"]},
            },
            {"kind": "primary_key", "name": "orders_key", "columns": ["id"], "references": None},
        ],
    }


def test_an_unknown_or_other_sources_object_is_404(client: TestClient) -> None:
    # CFX-APP-postgres/AC-09: the same database through another user is another source, with
    # objects of its own.
    source_id, _ = saved_source(client)
    other = {
        **DETAILS,
        "user": postgres_fixture.QUOTES_USER,
        "password": postgres_fixture.QUOTES_PASSWORD,
    }
    other_id = client.post("/api/sources/postgres", json=other).json()["id"]
    others = objects_in(client.get(f"/api/sources/{other_id}/objects").json())

    for object_id in (others["orders"]["id"], "0" * 32):
        response = client.get(f"/api/sources/{source_id}/objects/{object_id}")

        assert response.status_code == 404
        assert response.json() == {"detail": "There's no such object. Refresh the list."}


def chosen_in(listed: dict) -> set[str]:
    return {name for name, found in objects_in(listed).items() if found["chosen"]}


def test_choosing_objects_saves_exactly_them(client: TestClient) -> None:
    # CFX-APP-postgres/AC-11: the user chooses which objects to profile, and a second choice
    # replaces the first.
    source_id, listed = saved_source(client)
    path = f"/api/sources/{source_id}/selection"
    ids = [listed["orders"]["id"], listed["customer_names"]["id"]]
    assert client.put(path, json={"object_ids": ids}).status_code == 200

    response = client.put(path, json={"object_ids": [listed["customers"]["id"], *ids[:1]]})

    assert response.status_code == 200, response.text
    assert chosen_in(response.json()) == {"customers", "orders"}
    assert chosen_in(client.get(f"/api/sources/{source_id}/objects").json()) == {
        "customers",
        "orders",
    }


NOT_CHOOSABLE = (
    "Some of these objects can't be chosen: they have no read access, have never been populated,"
    " or are no longer in the database. Refresh and choose again."
)


def test_an_object_without_read_access_is_listed_and_cannot_be_chosen(client: TestClient) -> None:
    # CFX-APP-postgres/AC-10: listed, marked as not readable, and a selection with it is refused
    # whole.
    source_id, listed = saved_source(client)
    path = f"/api/sources/{source_id}/selection"
    client.put(path, json={"object_ids": [listed["orders"]["id"]]})
    assert listed["secret"]["readable"] is False
    assert listed["orders"]["readable"] is True

    response = client.put(
        path, json={"object_ids": [listed["customers"]["id"], listed["secret"]["id"]]}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": NOT_CHOOSABLE}
    assert chosen_in(client.get(f"/api/sources/{source_id}/objects").json()) == {"orders"}


def test_a_materialized_view_never_refreshed_is_listed_and_can_be_chosen_once_refreshed(
    client: TestClient, postgres_admin: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-10 and AC-13, as agreed for DRIVN-49: the role may SELECT from it, but
    # it has no rows to read until REFRESH MATERIALIZED VIEW, and a refresh in DrivnX shows that.
    source_id, listed = saved_source(client)
    path = f"/api/sources/{source_id}/selection"
    view = listed["customer_count"]
    assert (view["readable"], view["populated"]) == (True, False)
    assert listed["orders"]["populated"] is True

    response = client.put(path, json={"object_ids": [listed["orders"]["id"], view["id"]]})

    assert response.status_code == 422
    assert response.json() == {"detail": NOT_CHOOSABLE}
    assert chosen_in(client.get(f"/api/sources/{source_id}/objects").json()) == set()

    postgres_admin(f"REFRESH MATERIALIZED VIEW {SCHEMA}.customer_count")
    try:
        refreshed = objects_in(client.post(f"/api/sources/{source_id}/discover").json())
        chose = client.put(path, json={"object_ids": [view["id"]]})
    finally:
        postgres_admin(f"REFRESH MATERIALIZED VIEW {SCHEMA}.customer_count WITH NO DATA")

    assert refreshed["customer_count"]["populated"] is True
    assert chose.status_code == 200, chose.text
    assert chosen_in(chose.json()) == {"customer_count"}


def test_reading_the_list_or_an_object_changes_nothing(
    client: TestClient, workspace_dir: Path
) -> None:
    # CFX-APP-postgres/AC-08 and AC-09: GET routes only read, as every other one.
    source_id, listed = saved_source(client)
    client.put(
        f"/api/sources/{source_id}/selection", json={"object_ids": [listed["orders"]["id"]]}
    )

    def snapshot() -> list[str]:
        with closing(sqlite3.connect(workspace_dir / "workspace.db")) as connection:
            return list(connection.iterdump())

    before = snapshot()
    for path in (
        "objects",
        f"objects/{listed['orders']['id']}",
        f"objects/{listed['secret']['id']}",
        "objects/" + "0" * 32,
    ):
        assert client.get(f"/api/sources/{source_id}/{path}").status_code < 500

    assert snapshot() == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"object_ids": "one"},
        {"object_ids": [""]},
        {"object_ids": ["x" * 65]},
        {"object_ids": ["x"] * 100_001},
        {"object_ids": [], "schemas": ["public"]},
    ],
)
def test_a_selection_that_cant_be_right_is_refused_and_changes_nothing(
    client: TestClient, body: dict
) -> None:
    # CFX-APP-postgres/AC-11: only a list of object ids, refused as input before anything is read.
    source_id, listed = saved_source(client)
    path = f"/api/sources/{source_id}/selection"
    client.put(path, json={"object_ids": [listed["orders"]["id"]]})

    response = client.put(path, json=body)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert chosen_in(client.get(f"/api/sources/{source_id}/objects").json()) == {"orders"}


@pytest.mark.parametrize(
    ("method", "path", "body", "says"),
    [
        ("GET", "/api/sources/{id}/rows", None,
         "Previewing a database's tables isn't available yet."),
        ("POST", "/api/profile-runs", {"source_id": "{id}", "mode": "fast"},
         "Profiling a database's tables isn't available yet."),
    ],
)
def test_previewing_and_profiling_a_database_no_longer_wait_for_choosing(
    client: TestClient, method: str, path: str, body: dict | None, says: str
) -> None:
    # CFX-APP-postgres/AC-11: choosing is here, so the message no longer says it isn't; previewing
    # (DRIVN-50) and profiling (DRIVN-51) a chosen object come next.
    source_id, _ = saved_source(client)
    filled = {key: value.replace("{id}", source_id) for key, value in body.items()} if body else None

    response = client.request(method, path.format(id=source_id), json=filled)

    assert response.status_code == 409
    assert response.json() == {"detail": says}


MANY = "drivnx_many"
TABLES = 1000
BATCH = 250


@pytest.fixture
def many_tables(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    """1,000 tables of three columns and a primary key each, made in batches so no transaction
    holds too many locks."""
    batches = [range(start, start + BATCH) for start in range(1, TABLES + 1, BATCH)]

    def drop() -> None:
        for batch in batches:
            names = ", ".join(f"{MANY}.t{number}" for number in batch)
            postgres_admin(f"DROP TABLE IF EXISTS {names}")
        postgres_admin(f"DROP SCHEMA IF EXISTS {MANY}")

    drop()
    postgres_admin(f"CREATE SCHEMA {MANY}")
    for batch in batches:
        # One statement, as the superuser's helper runs one at a time.
        postgres_admin(
            f"DO $$ BEGIN FOR i IN {batch.start}..{batch.stop - 1} LOOP EXECUTE format("
            f"'CREATE TABLE {MANY}.t%s (id integer PRIMARY KEY, a text, b bigint)', i);"
            " END LOOP; END $$"
        )
    postgres_admin(f"GRANT USAGE ON SCHEMA {MANY} TO drivnx_fixture")
    postgres_admin(f"GRANT SELECT ON ALL TABLES IN SCHEMA {MANY} TO drivnx_fixture")
    try:
        yield
    finally:
        drop()


def test_discovering_1000_objects_takes_at_most_5_seconds(
    client: TestClient, many_tables: None
) -> None:
    # CFX-APP-postgres non-functional: listing 1,000 objects within 5 s and an object's columns
    # within 2 s (AC-08, AC-09). The times are recorded in DRIVN-49.
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]

    started = time.perf_counter()
    refreshed = client.post(f"/api/sources/{source_id}/discover")
    listed = client.get(f"/api/sources/{source_id}/objects")
    listing = time.perf_counter() - started
    many = objects_in(listed.json(), MANY)
    started = time.perf_counter()
    opened = client.get(f"/api/sources/{source_id}/objects/{many['t500']['id']}")
    opening = time.perf_counter() - started
    print(f"\nDiscover and list {len(many)} objects: {listing:.3f} s; open one: {opening:.3f} s")

    assert refreshed.status_code == 200 and listed.status_code == 200 and opened.status_code == 200
    assert len(many) == TABLES
    assert [column["name"] for column in opened.json()["columns"]] == ["id", "a", "b"]
    assert listing <= 5
    assert opening <= 2


ROUTES = [
    ("POST", "/api/sources/{id}/discover", None),
    ("GET", "/api/sources/{id}/objects", None),
    ("GET", "/api/sources/{id}/objects/" + "0" * 32, None),
    ("PUT", "/api/sources/{id}/selection", {"object_ids": []}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_file_has_no_objects_and_an_unknown_source_none_either(
    client: TestClient, tmp_path: Path, method: str, path: str, body: dict | None
) -> None:
    # CFX-APP-postgres/AC-08: only a database has schemas and tables.
    csv = tmp_path / "people.csv"
    csv.write_text("id\n1\n", encoding="utf-8")
    file_id = client.post("/api/sources", json={"path": str(csv)}).json()["id"]

    for source_id, status, says in (
        (file_id, 409, "A file has no schemas or tables."),
        ("0" * 32, 404, "There's no such source."),
    ):
        response = client.request(method, path.format(id=source_id), json=body)

        assert response.status_code == status, path
        assert response.json() == {"detail": says}


def restarted(start_drivnx: StartDrivnX, directory: Path) -> tuple[httpx.Client, dict]:
    """DrivnX with a saved connection whose objects are listed, stopped and started again, so it
    doesn't know the password. The saved source's objects as they were, by name."""
    before = start_drivnx("--no-browser", workspace=directory)
    first = sign_in(before.wait_for_launch())
    source_id = first.post("/api/sources/postgres", json=DETAILS).json()["id"]
    listed = objects_in(first.get(f"/api/sources/{source_id}/objects").json())
    first.put(
        f"/api/sources/{source_id}/selection",
        json={"object_ids": [listed["orders"]["id"], listed["customer_names"]["id"]]},
    )
    saved = first.get(f"/api/sources/{source_id}/objects").json()
    before.process.terminate()
    before.process.wait(timeout=30)
    return sign_in(start_drivnx("--no-browser", workspace=directory).wait_for_launch()), saved


def test_listing_again_needs_the_password(start_drivnx: StartDrivnX, tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-13: a refresh reads the database, so after a restart it waits for the
    # password, as the source asks for it (AC-05).
    after, _ = restarted(start_drivnx, tmp_path / "workspace")
    (source,) = after.get("/api/sources").json()

    response = after.post(f"/api/sources/{source['id']}/discover")

    assert response.status_code == 409
    assert response.json() == {"detail": "Enter the password for this database first."}


def test_a_saved_selection_stays_after_a_restart(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-11: kept in the workspace, so it is there before the password is given.
    after, saved = restarted(start_drivnx, tmp_path / "workspace")
    (source,) = after.get("/api/sources").json()
    assert source["password_needed"] is True

    response = after.get(f"/api/sources/{source['id']}/objects")

    assert response.status_code == 200
    assert response.json() == saved
    assert chosen_in(saved) == {"orders", "customer_names"}


CHANGING = "drivnx_api_changing"


@pytest.fixture
def changing(postgres_admin: Callable[[str], None]) -> Iterator[Callable[[str], None]]:
    """A schema of this test's own, with a table to drop and one to change."""
    drop = f"DROP SCHEMA IF EXISTS {CHANGING} CASCADE"
    postgres_admin(drop)
    for statement in (
        f"CREATE SCHEMA {CHANGING}",
        f"CREATE TABLE {CHANGING}.doomed (id integer)",
        f"CREATE TABLE {CHANGING}.changing (id integer, label text)",
        f"GRANT USAGE ON SCHEMA {CHANGING} TO drivnx_fixture",
        f"GRANT SELECT ON {CHANGING}.doomed, {CHANGING}.changing TO drivnx_fixture",
    ):
        postgres_admin(statement)
    try:
        yield postgres_admin
    finally:
        postgres_admin(drop)


def test_refreshing_after_a_drop_and_a_column_change_shows_the_database_as_it_is_now(
    client: TestClient, changing: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-13 (the refresh) and AC-11: a dropped table is gone, a changed one is a
    # new object with its new columns, and it stays chosen.
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    before = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), CHANGING)
    client.put(
        f"/api/sources/{source_id}/selection", json={"object_ids": [before["changing"]["id"]]}
    )
    changing(f"DROP TABLE {CHANGING}.doomed")
    changing(f"ALTER TABLE {CHANGING}.changing ALTER COLUMN id TYPE bigint")
    changing(f"ALTER TABLE {CHANGING}.changing ADD COLUMN note text")

    response = client.post(f"/api/sources/{source_id}/discover")

    assert response.status_code == 200, response.text
    after = objects_in(response.json(), CHANGING)
    assert after.keys() == {"changing"}
    assert after["changing"]["id"] != before["changing"]["id"]
    assert after["changing"]["chosen"] is True
    opened = client.get(f"/api/sources/{source_id}/objects/{after['changing']['id']}").json()
    assert [(column["name"], column["physical_type"]) for column in opened["columns"]] == [
        ("id", "bigint"),
        ("label", "text"),
        ("note", "text"),
    ]
    for gone in (before["doomed"], before["changing"]):
        assert client.get(f"/api/sources/{source_id}/objects/{gone['id']}").status_code == 404


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_refresh_that_cant_reach_the_server_says_why_and_keeps_the_list(
    client: TestClient, workspace_dir: Path
) -> None:
    # CFX-APP-postgres/AC-13: the server moved after the source was saved. The connector's message,
    # never DuckDB's text, and the objects listed before stay.
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    listed = client.get(f"/api/sources/{source_id}/objects").json()
    with closing(sqlite3.connect(workspace_dir / "workspace.db")) as connection, connection:
        connection.execute("UPDATE source SET port = ? WHERE id = ?", (unused_port(), source_id))

    response = client.post(f"/api/sources/{source_id}/discover")

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Nothing answered at this host and port. Check them, and that the server is"
        " running."
    }
    assert client.get(f"/api/sources/{source_id}/objects").json() == listed
