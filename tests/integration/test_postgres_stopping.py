"""A database's run stopped while it reads: by the user, at its time limit, or by the server.
CFX-APP-postgres AC-20, AC-21 and AC-22, and ADR 0009 (DRIVN-53)."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from artifacts.workspace import Workspace
from connectors.csv_files import duckdb_connection
from connectors import postgres
from connectors.postgres import load_postgres
from fastapi.testclient import TestClient
from local_api import profile_jobs

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
# CFX-APP-postgres/AC-20: "its scan stops within 5 s".
STOP_AT_THE_LIMIT_WITHIN_SECONDS = 5
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
    # As `late_sleepy`, but its whole read takes 3 s.
    f"CREATE VIEW {SCHEMA}.three_seconds AS SELECT n FROM generate_series(1, 100001) AS n"
    " WHERE n <= 100000 OR (SELECT true FROM pg_sleep(3))",
    # Read in about 2 s by a fast reading, and again by a full read.
    f"CREATE VIEW {SCHEMA}.two_seconds AS SELECT 1 AS n FROM pg_sleep(2)",
    # Only the test that revokes it chooses it.
    f"CREATE TABLE {SCHEMA}.withdrawn (id integer)",
    f"CREATE TABLE {SCHEMA}.small (id integer)",
    f"INSERT INTO {SCHEMA}.small VALUES (1), (2), (3)",
    f"ANALYZE {SCHEMA}.small",
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


@pytest.mark.parametrize("name", ["late_sleepy", "big"])
def test_an_object_reaching_its_time_limit_is_stopped_and_the_run_goes_on(
    start_app: StartApp,
    workspace: Workspace,
    drivnx_queries: Callable[[str], int],
    escaped_errors: list[BaseException | None],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    # CFX-APP-postgres/AC-20: "its scan stops within 5 s, no query from DrivnX is left running
    # on the server, the object shows 'stopped at the time limit, no results', the run goes on
    # to the next object, and the finished run says which objects have no results". A minute
    # lasts a second here, and each object takes longer than that to read whole.
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", 1)
    client = start_app(None)
    source_id = chosen_source(client, name, "small")
    stopped, small = f"{SCHEMA}.{name}", f"{SCHEMA}.small"

    started_at = time.monotonic()
    started = client.post(
        "/api/profile-runs",
        json={"source_id": source_id, "mode": "full", "time_limit_minutes": 1},
    )
    assert started.status_code == 202, started.text
    run = ended(client, started.json()["id"])
    took = time.monotonic() - started_at

    assert (run["status"], run["failure"]) == ("completed", None)
    assert took < 1 + STOP_AT_THE_LIMIT_WITHIN_SECONDS, took
    assert drivnx_queries() == 0
    detail = client.get(f"/api/profile-runs/{run['id']}").json()
    flagged = {found["object"]: found.get("stopped_at_time_limit") for found in detail["objects"]}
    assert flagged == {stopped: True, small: False}
    metrics = {
        (seen["object"], seen["column"], seen["metric"]): seen["value"]
        for seen in detail["metrics"]
    }
    assert {key: value for key, value in metrics.items() if key[0] == stopped} == {
        (stopped, None, "time_limit_reached"): 1
    }
    assert metrics[(small, None, "rows_read")] == 3
    assert escaped_errors == []


def test_a_full_profiles_read_may_take_longer_than_other_statements_within_its_limit(
    start_app: StartApp, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ADR 0009 Decision 3: "A full run attaches with a statement_timeout of the time limit and a
    # margin, as a view's one statement can take longer than the 30 s other reads have". Here
    # other statements have 1 s, and the limit is 10 "minutes" of a second each.
    monkeypatch.setattr(postgres, "STATEMENT_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", 1)
    client = start_app(None)
    source_id = chosen_source(client, "three_seconds")

    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "full"})
    assert started.status_code == 202, started.text
    run = ended(client, started.json()["id"])

    assert (run["status"], run["failure"]) == ("completed", None)
    detail = client.get(f"/api/profile-runs/{run['id']}").json()
    rows_read = {
        seen["object"]: seen["value"] for seen in detail["metrics"] if seen["metric"] == "rows_read"
    }
    assert rows_read == {f"{SCHEMA}.three_seconds": 100_001}


# How each read of a slow view reaches the server, as in the cancel's test.
SLOW_READS = [
    ("fast", "sleepy", f'COPY (SELECT "n"::text AS c0 FROM "{SCHEMA}"."sleepy"%'),
    ("full", "late_sleepy", f'COPY (SELECT "n" FROM "{SCHEMA}"."late_sleepy"%'),
]


def reading(drivnx_queries: Callable[[str], int], read: str) -> None:
    """Wait until DrivnX is running this read on the server."""
    deadline = time.monotonic() + 30
    while not drivnx_queries(read) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert drivnx_queries(read) == 1, "The read never started on the server."


@pytest.mark.parametrize(("mode", "name", "read"), SLOW_READS)
def test_a_run_whose_session_the_server_ends_mid_read_fails_naming_the_object_and_why(
    start_app: StartApp,
    workspace: Workspace,
    postgres_admin: Callable[[str], None],
    drivnx_queries: Callable[[str], int],
    mode: str,
    name: str,
    read: str,
) -> None:
    # CFX-APP-postgres/AC-22: "Given a connection lost, a server restart ... during a run, then
    # the run ends `failed`, the message names the object and the reason, and its results don't
    # appear as current". A restart's fast shutdown ends each session as this does (decided for
    # DRIVN-53), and the server says the same.
    client = start_app(None)
    source_id = chosen_source(client, name)
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": mode})
    assert started.status_code == 202, started.text
    reading(drivnx_queries, read)

    postgres_admin(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
        " WHERE application_name = 'DrivnX' AND state = 'active'"
    )
    run = ended(client, started.json()["id"])

    assert (run["status"], run["failure"]) == (
        "failed",
        f"{SCHEMA}.{name}: DrivnX lost its connection to the database, as when the server"
        " restarts or an administrator ends the session. Profile again once it's back.",
    )
    assert client.get(f"/api/profile-runs/{run['id']}").json()["metrics"] == []


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_a_run_whose_next_object_is_revoked_mid_run_fails_naming_it_and_why(
    start_app: StartApp,
    workspace: Workspace,
    postgres_admin: Callable[[str], None],
    drivnx_queries: Callable[[str], int],
    mode: str,
) -> None:
    # CFX-APP-postgres/AC-22: "Given ... a privilege revoked during a run, then the run ends
    # `failed`, the message names the object and the reason, and its results don't appear as
    # current". Revoked while the object before it is read.
    client = start_app(None)
    source_id = chosen_source(client, "two_seconds", "withdrawn")
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": mode})
    assert started.status_code == 202, started.text
    reading(drivnx_queries, f'COPY (SELECT "n"::text AS c0 FROM "{SCHEMA}"."two_seconds"%')

    postgres_admin(f"REVOKE SELECT ON {SCHEMA}.withdrawn FROM drivnx_fixture")
    try:
        run = ended(client, started.json()["id"])
    finally:
        postgres_admin(f"GRANT SELECT ON {SCHEMA}.withdrawn TO drivnx_fixture")

    assert (run["status"], run["failure"]) == (
        "failed",
        f"{SCHEMA}.withdrawn: This connection's user isn't allowed to read this object any more."
        " Ask the database's administrator for SELECT on it, or leave it out.",
    )
    assert client.get(f"/api/profile-runs/{run['id']}").json()["metrics"] == []


def test_a_profile_read_the_server_stops_for_taking_too_long_fails_naming_the_object(
    start_app: StartApp, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-postgres/AC-22 with ADR 0009 Decision 6: a statement timeout is told apart from a
    # lost connection and a revoked privilege, and is about the object, not a preview's page.
    # Other statements have 1 s here, and a fast reading of the view takes 2 s.
    monkeypatch.setattr(postgres, "STATEMENT_TIMEOUT_SECONDS", 1)
    client = start_app(None)
    source_id = chosen_source(client, "two_seconds")

    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    assert started.status_code == 202, started.text
    run = ended(client, started.json()["id"])

    assert (run["status"], run["failure"]) == (
        "failed",
        f"{SCHEMA}.two_seconds: The database took too long to read this object, and stopped."
        " Try again when it's less busy.",
    )


def test_a_running_jobs_password_stays_out_of_its_repr(
    start_app: StartApp, workspace: Workspace
) -> None:
    # CFX-APP-postgres, Non-functional "No secret is saved outside the OS credential store", and
    # ADR 0007: the job holds the password to cancel its read on the server (ADR 0009), and a
    # debugger or a test's failure shows the jobs as their repr (found in review).
    client = start_app(None)
    source_id = chosen_source(client, "sleepy")
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = profile_jobs._jobs.get(run_id)
        if job is not None and job.cancel_on_server is not None:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("The job never held a way to cancel its read.")

    shown = repr(profile_jobs._jobs)

    client.post(f"/api/profile-runs/{run_id}/cancel")
    assert ended(client, run_id)["status"] == "cancelled"
    assert postgres_fixture.PASSWORD not in shown
