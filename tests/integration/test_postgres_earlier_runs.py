"""A database's earlier runs, what they describe, and restarts, DRIVN-45 AC-31, AC-32 and
AC-33 (DRIVN-57)."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from artifacts.profiles import mark_interrupted_runs
from artifacts.workspace import Workspace
from fastapi.testclient import TestClient
from local_api.profile_jobs import stop_jobs

from ..conftest import StartDrivnX, posix_only
from .test_credential_store import NO_STORE
from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_discovery import server_on_windows
from .test_postgres_password_store import (  # noqa: F401 (the fixtures)
    StartApp,
    start_app,
    workspace,
)
from .test_postgres_profile_api import ended
from .test_postgres_stopping import (  # noqa: F401 (the fixture)
    SLEEP_SECONDS,
    drivnx_queries,
    reading,
)
from .test_session import sign_in

# DRIVN-45, Non-functional: "cancelling stops a run within 5 s", which a stop is too.
GONE_WITHIN_SECONDS = 5

SCHEMA = "drivnx_earlier_runs"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.orders (id integer, total numeric)",
    f"INSERT INTO {SCHEMA}.orders VALUES (1, 9.5), (2, 12)",
    f"CREATE TABLE {SCHEMA}.customers (id integer, name text)",
    f"INSERT INTO {SCHEMA}.customers VALUES (1, 'Ann'), (2, 'Bo')",
    f"CREATE VIEW {SCHEMA}.big_orders AS SELECT * FROM {SCHEMA}.orders WHERE total > 10",
    f"ANALYZE {SCHEMA}.orders",
    f"ANALYZE {SCHEMA}.customers",
    # Only the test that changes them chooses these.
    f"CREATE TABLE {SCHEMA}.widened (id integer)",
    f"CREATE TABLE {SCHEMA}.retyped (id integer)",
    f"CREATE TABLE {SCHEMA}.dropped (id integer)",
    f"CREATE TABLE {SCHEMA}.steady (id integer)",
    f"INSERT INTO {SCHEMA}.widened VALUES (1)",
    f"INSERT INTO {SCHEMA}.retyped VALUES (1)",
    f"INSERT INTO {SCHEMA}.dropped VALUES (1)",
    f"INSERT INTO {SCHEMA}.steady VALUES (1)",
    # 3 million rows, which the scanner takes a few seconds to read whole (ADR 0009, Measured), and
    # sends as it reads them.
    f"CREATE TABLE {SCHEMA}.big (id integer, code text)",
    f"INSERT INTO {SCHEMA}.big SELECT n, 'C-' || n FROM generate_series(1, 3000000) AS n",
    f"ANALYZE {SCHEMA}.big",
    # Its first 100,000 rows come at once, for a full profile's fast reading, and its whole read's
    # last row only after the sleep, so the server computes without sending anything meanwhile.
    f"CREATE VIEW {SCHEMA}.late_sleepy AS SELECT n FROM generate_series(1, 100001) AS n"
    f" WHERE n <= 100000 OR (SELECT true FROM pg_sleep({SLEEP_SECONDS}))",
    # A fast profile reads it in one statement, which sends nothing until the sleep ends.
    f"CREATE VIEW {SCHEMA}.sleepy AS SELECT 1 AS n FROM pg_sleep({SLEEP_SECONDS})",
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


def choose(client: httpx.Client, source_id: str, *names: str) -> None:
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    response = client.put(
        f"/api/sources/{source_id}/selection",
        json={"object_ids": [listed[name]["id"] for name in names]},
    )
    assert response.status_code == 200, response.text


def completed_run(client: httpx.Client, source_id: str, mode: str) -> dict:
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": mode})
    assert started.status_code == 202, started.text
    run = ended(client, started.json()["id"])
    assert run["status"] == "completed", run
    return run


def test_a_database_run_in_the_list_names_the_objects_it_read(client: TestClient) -> None:
    # DRIVN-45/AC-31: "the user can list them with mode, status, time and objects".
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    choose(client, source_id, "orders", "customers", "big_orders")

    run = completed_run(client, source_id, "fast")

    (listed,) = client.get("/api/profile-runs").json()
    assert listed["id"] == run["id"]
    # As `schema.object`, in the order the run read them, which its detail shows too.
    detail = client.get(f"/api/profile-runs/{run['id']}").json()
    assert listed["object_names"] == [found["object"] for found in detail["objects"]]
    assert sorted(listed["object_names"]) == [
        f"{SCHEMA}.big_orders",
        f"{SCHEMA}.customers",
        f"{SCHEMA}.orders",
    ]


def test_every_run_of_a_database_is_listed_newest_first_and_any_completed_one_opens(
    client: TestClient,
) -> None:
    # DRIVN-45/AC-31: "Given several runs of a source, then the user can list them with
    # mode, status, time and objects, open any completed one".
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    choose(client, source_id, "orders")
    first = completed_run(client, source_id, "fast")
    choose(client, source_id, "orders", "customers")
    second = completed_run(client, source_id, "full")
    choose(client, source_id, "big_orders")
    third = completed_run(client, source_id, "fast")

    listed = [run for run in client.get("/api/profile-runs").json() if run["source_id"] == source_id]

    assert [
        (run["id"], run["mode"], run["status"], sorted(run["object_names"])) for run in listed
    ] == [
        (third["id"], "fast", "completed", [f"{SCHEMA}.big_orders"]),
        (second["id"], "full", "completed", [f"{SCHEMA}.customers", f"{SCHEMA}.orders"]),
        (first["id"], "fast", "completed", [f"{SCHEMA}.orders"]),
    ]
    assert [run["started_at"] for run in listed] == sorted(
        (run["started_at"] for run in listed), reverse=True
    )
    oldest = client.get(f"/api/profile-runs/{first['id']}")
    assert oldest.status_code == 200, oldest.text
    assert {metric["object"] for metric in oldest.json()["metrics"]} == {f"{SCHEMA}.orders"}


def test_a_files_run_names_no_objects(client: TestClient, tmp_path: Path) -> None:
    # DRIVN-45/AC-31: only a database's run reads objects.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n", encoding="utf-8", newline="")
    source_id = client.post("/api/sources", json={"path": str(path)}).json()["id"]

    completed_run(client, source_id, "fast")

    (listed,) = client.get("/api/profile-runs").json()
    assert listed["object_names"] is None


def out_of_date(client: TestClient, run: dict) -> dict[str, bool]:
    detail = client.get(f"/api/profile-runs/{run['id']}").json()
    return {found["object"]: found["out_of_date"] for found in detail["objects"]}


def test_an_object_whose_columns_or_types_changed_is_out_of_date_once_the_list_is_refreshed(
    client: TestClient, postgres_admin: Callable[[str], None]
) -> None:
    # DRIVN-45/AC-32: "Given an object whose columns or types differ from the run's, then
    # the Profile tab says the results are out of date". Known from the refresh (decided for
    # DRIVN-57), the rule AC-13 refreshes by.
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    choose(client, source_id, "widened", "retyped", "dropped", "steady")
    run = completed_run(client, source_id, "fast")
    postgres_admin(f"ALTER TABLE {SCHEMA}.widened ADD COLUMN note text")
    postgres_admin(f"ALTER TABLE {SCHEMA}.retyped ALTER COLUMN id TYPE bigint")
    postgres_admin(f"DROP TABLE {SCHEMA}.dropped")

    # Until the list is refreshed, DrivnX doesn't know.
    assert set(out_of_date(client, run).values()) == {False}

    refreshed = client.post(f"/api/sources/{source_id}/discover")
    assert refreshed.status_code == 200, refreshed.text

    assert out_of_date(client, run) == {
        f"{SCHEMA}.dropped": True,
        f"{SCHEMA}.retyped": True,
        f"{SCHEMA}.steady": False,
        f"{SCHEMA}.widened": True,
    }


def test_a_refresh_that_finds_nothing_changed_leaves_every_object_current(
    client: TestClient,
) -> None:
    # DRIVN-45/AC-32: only an object whose columns or types differ is out of date.
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    choose(client, source_id, "orders", "customers", "big_orders")
    run = completed_run(client, source_id, "fast")

    refreshed = client.post(f"/api/sources/{source_id}/discover")
    assert refreshed.status_code == 200, refreshed.text

    assert out_of_date(client, run) == {
        f"{SCHEMA}.big_orders": False,
        f"{SCHEMA}.customers": False,
        f"{SCHEMA}.orders": False,
    }
    assert client.get(f"/api/profile-runs/{run['id']}").json()["current_settings"] is True


def none_left_on_the_server(drivnx_queries: Callable[[str], int], since: float) -> None:
    """Nothing DrivnX started is running on the server within the target from `since`."""
    while drivnx_queries("%") and time.monotonic() < since + GONE_WITHIN_SECONDS:
        time.sleep(0.05)
    assert drivnx_queries("%") == 0, "A query DrivnX started is still running on the server."


@pytest.mark.parametrize(
    ("name", "read"),
    [
        pytest.param("big", f'COPY (SELECT "id", "code" FROM "{SCHEMA}"."big"%', id="streaming"),
        # Windows' `terminate()` is a hard kill, not a stop, which the test after this one is.
        pytest.param(
            "late_sleepy",
            f'COPY (SELECT "n" FROM "{SCHEMA}"."late_sleepy"%',
            id="computing",
            marks=posix_only,
        ),
    ],
)
def test_a_stop_during_a_database_run_keeps_completed_runs_and_leaves_nothing_on_the_server(
    start_drivnx: StartDrivnX,
    tmp_path: Path,
    drivnx_queries: Callable[[str], int],
    name: str,
    read: str,
) -> None:
    # DRIVN-45/AC-33: "Given a restart, then completed runs, their metrics and findings are
    # as they were, a run that was in progress shows as `interrupted`, and no query DrivnX started
    # is still running on the server." Through the real command, stopped mid-read.
    directory = tmp_path / "workspace"
    before = start_drivnx("--no-browser", env=NO_STORE, workspace=directory)
    client = sign_in(before.wait_for_launch())
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    choose(client, source_id, "orders", "customers")
    completed = completed_run(client, source_id, "fast")
    detail = client.get(f"/api/profile-runs/{completed['id']}").json()
    findings = client.get("/api/findings", params={"profile_run_id": completed["id"]}).json()
    # Some, so the comparison after the restart shows they're kept (DRIVN-55).
    assert findings
    choose(client, source_id, name)
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "full"})
    assert started.status_code == 202, started.text
    reading(drivnx_queries, read)

    before.process.terminate()
    stopped_at = time.monotonic()
    before.process.wait(timeout=30)
    after = sign_in(
        start_drivnx("--no-browser", env=NO_STORE, workspace=directory).wait_for_launch()
    )

    none_left_on_the_server(drivnx_queries, stopped_at)
    listed = after.get("/api/profile-runs").json()
    assert [(run["id"], run["status"]) for run in listed] == [
        (started.json()["id"], "interrupted"),
        (completed["id"], "completed"),
    ]
    assert after.get(f"/api/profile-runs/{completed['id']}").json() == detail
    assert (
        after.get("/api/findings", params={"profile_run_id": completed["id"]}).json() == findings
    )

def test_drivnx_killed_during_a_database_run_leaves_nothing_on_the_server_after_a_restart(
    start_drivnx: StartDrivnX,
    tmp_path: Path,
    drivnx_queries: Callable[[str], int],
) -> None:
    # DRIVN-45/AC-33: "Given a restart, then ... a run that was in progress shows as
    # `interrupted`, and no query DrivnX started is still running on the server." Killed, so it
    # can't cancel anything: the server checks its connection is still there (decided for
    # DRIVN-57). Only where the server can (ADR 0009 Decision 7).
    if server_on_windows(tmp_path / "version"):
        pytest.skip("PostgreSQL on Windows can't check a connection (ADR 0009).")
    directory = tmp_path / "workspace"
    before = start_drivnx("--no-browser", env=NO_STORE, workspace=directory)
    client = sign_in(before.wait_for_launch())
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    choose(client, source_id, "sleepy")
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    assert started.status_code == 202, started.text
    reading(drivnx_queries, f'COPY (SELECT "n"::text AS c0 FROM "{SCHEMA}"."sleepy"%')

    before.process.kill()
    killed_at = time.monotonic()
    before.process.wait(timeout=30)
    after = sign_in(
        start_drivnx("--no-browser", env=NO_STORE, workspace=directory).wait_for_launch()
    )

    none_left_on_the_server(drivnx_queries, killed_at)
    (listed,) = after.get("/api/profile-runs").json()
    assert (listed["id"], listed["status"]) == (started.json()["id"], "interrupted")


@pytest.mark.parametrize(
    ("mode", "name", "read"),
    [
        ("fast", "sleepy", f'COPY (SELECT "n"::text AS c0 FROM "{SCHEMA}"."sleepy"%'),
        ("full", "late_sleepy", f'COPY (SELECT "n" FROM "{SCHEMA}"."late_sleepy"%'),
    ],
)
def test_stopping_drivnx_cancels_a_database_runs_read_on_the_server(
    start_app: StartApp,
    workspace: Workspace,
    drivnx_queries: Callable[[str], int],
    mode: str,
    name: str,
    read: str,
) -> None:
    # DRIVN-45/AC-33: "no query DrivnX started is still running on the server". What
    # shutdown does (`stop_jobs()`), in a process that's still running, so its session is still
    # open and the server's own check (ADR 0009 Decision 7) can't be what ends the read: a read
    # that computes before its rows, which DuckDB's interrupt alone can't stop (Decision 1).
    client = start_app(None)
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    choose(client, source_id, name)
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": mode})
    assert started.status_code == 202, started.text
    reading(drivnx_queries, read)

    stopped_at = time.monotonic()
    stop_jobs()

    none_left_on_the_server(drivnx_queries, stopped_at)
    # Left for the next start to mark, as only it knows nothing is still writing the run.
    (listed,) = client.get("/api/profile-runs").json()
    assert listed["status"] == "running"
    mark_interrupted_runs(workspace.engine)
    (listed,) = client.get("/api/profile-runs").json()
    assert (listed["id"], listed["status"]) == (started.json()["id"], "interrupted")
