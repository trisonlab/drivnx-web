"""Stopping a database's run while it reads, CFX-APP-postgres AC-21 and ADR 0009 (DRIVN-53)."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from artifacts.workspace import Workspace
from connectors.csv_files import duckdb_connection
from connectors.postgres import load_postgres
from fastapi.testclient import TestClient

from .. import postgres_fixture
from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_password_store import (  # noqa: F401 (the fixtures)
    StartApp,
    start_app,
    workspace,
)
from .test_postgres_profile_api import ended

# CFX-APP-postgres, Non-functional: "cancelling stops a run within 5 s".
CANCEL_WITHIN_SECONDS = 5
# Longer than the cancel's target, and shorter than the 30 s any one statement may run.
SLEEP_SECONDS = 20
SCHEMA = "drivnx_stopping"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    # A fast profile reads a view in one `postgres_query`, which DuckDB's interrupt can't stop.
    f"CREATE VIEW {SCHEMA}.sleepy AS SELECT 1 AS n FROM pg_sleep({SLEEP_SECONDS})",
    # Its first 100,000 rows come at once, so a full profile's fast reading of it is quick, and
    # the 100,001st only after the sleep, so its whole read through the scanner, one `COPY`,
    # isn't.
    f"CREATE VIEW {SCHEMA}.late_sleepy AS SELECT n FROM generate_series(1, 100001) AS n"
    f" WHERE n <= 100000 OR (SELECT true FROM pg_sleep({SLEEP_SECONDS}))",
    # 3 million rows, which the scanner takes a few seconds to read whole (ADR 0009, Measured).
    f"CREATE TABLE {SCHEMA}.big (id integer, code text)",
    f"INSERT INTO {SCHEMA}.big SELECT n, 'C-' || n FROM generate_series(1, 3000000) AS n",
    f"ANALYZE {SCHEMA}.big",
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


def chosen_source(client: TestClient, *names: str) -> str:
    """A saved connection to the fixture with these objects of this test's schema chosen."""
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    response = client.put(
        f"/api/sources/{source_id}/selection",
        json={"object_ids": [listed[name]["id"] for name in names]},
    )
    assert response.status_code == 200, response.text
    return source_id


# DrivnX's queries running on the server whose text is like a pattern, as its superuser sees them.
ACTIVE = (
    "SELECT * FROM postgres_query('admin', 'SELECT count(*) FROM pg_stat_activity"
    " WHERE application_name = ''DrivnX'' AND state = ''active'' AND query LIKE ''{}''')"
)


@pytest.fixture
def drivnx_queries(tmp_path: Path) -> Iterator[Callable[[str], int]]:
    """How many queries DrivnX has running on the fixture's server now, like a pattern."""
    admin = postgres_fixture.dsn(postgres_fixture.SUPERUSER, postgres_fixture.SUPERUSER_PASSWORD)
    with duckdb_connection(tmp_path / "admin") as connection:
        load_postgres(connection)
        connection.execute(f"ATTACH '{admin}' AS admin (TYPE postgres)")
        yield lambda pattern="%": connection.execute(ACTIVE.format(pattern)).fetchone()[0]


@pytest.mark.parametrize(
    ("mode", "name", "read"),
    [
        # How each read reaches the server: a fast reading selects each value as `::text`, the
        # scanner's whole read the columns themselves.
        ("fast", "sleepy", f'COPY (SELECT "n"::text AS c0 FROM "{SCHEMA}"."sleepy"%'),
        ("full", "late_sleepy", f'COPY (SELECT "n" FROM "{SCHEMA}"."late_sleepy"%'),
        # A table's scan is in chunks, which DuckDB's interrupt stops between.
        ("full", "big", f'COPY (SELECT "id", "code" FROM "{SCHEMA}"."big"%'),
    ],
)
def test_cancelling_a_run_mid_read_stops_the_read_on_the_server(
    start_app: StartApp,
    workspace: Workspace,
    drivnx_queries: Callable[[str], int],
    escaped_errors: list[BaseException | None],
    mode: str,
    name: str,
    read: str,
) -> None:
    # CFX-APP-postgres/AC-21: "it stops within the target under Non-functional, no query from
    # DrivnX is left running on the server, its status is `cancelled`, and none of its results
    # or findings appear as current".
    client = start_app(None)
    source_id = chosen_source(client, name)
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": mode})
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    deadline = time.monotonic() + 30
    while not drivnx_queries(read) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert drivnx_queries(read) == 1, "The read never started on the server."

    cancelled_at = time.monotonic()
    response = client.post(f"/api/profile-runs/{run_id}/cancel")
    run = ended(client, run_id)
    took = time.monotonic() - cancelled_at

    assert response.status_code == 202, response.text
    assert took < CANCEL_WITHIN_SECONDS, took
    assert (run["status"], run["failure"]) == ("cancelled", None)
    assert drivnx_queries() == 0
    assert client.get(f"/api/profile-runs/{run_id}").json()["metrics"] == []
    assert escaped_errors == []
