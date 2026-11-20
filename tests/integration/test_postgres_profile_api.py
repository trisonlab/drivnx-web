"""A fast profile of a database's chosen objects through the API, CFX-APP-postgres AC-13, AC-17,
AC-18 and AC-24 (DRIVN-51)."""

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from artifacts.profiles import read_findings, read_observations, read_profile_run
from artifacts.sources import StoredPostgresSource, find_source, read_chosen_objects
from artifacts.workspace import Workspace
from connectors.csv_files import duckdb_connection
from connectors.postgres import load_postgres, reading_for
from fastapi.testclient import TestClient
from local_api import profile_jobs
from local_api.postgres_api import details_of

from .. import postgres_fixture
from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_password_store import (  # noqa: F401 (the fixtures)
    REFUSED,
    StartApp,
    saved_with_a_wrong_stored_password,
    start_app,
    workspace,
)

SCHEMA = "drivnx_api_profile"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.people (id integer PRIMARY KEY, name text, notes jsonb)",
    f"INSERT INTO {SCHEMA}.people VALUES (1, 'Ann', '{{}}'), (2, '', NULL), (3, NULL, '[1]')",
    f"ANALYZE {SCHEMA}.people",
    f"CREATE VIEW {SCHEMA}.names AS SELECT name FROM {SCHEMA}.people",
    # Only the test that changes it chooses it, and the same for `revoked`.
    f"CREATE TABLE {SCHEMA}.changing (id integer, label text)",
    f"CREATE TABLE {SCHEMA}.revoked (id integer)",
    # Analysed empty, then filled past the 100,001-row guard; autovacuum mustn't fix the estimate.
    f"CREATE TABLE {SCHEMA}.misestimated (id integer) WITH (autovacuum_enabled = false)",
    f"ANALYZE {SCHEMA}.misestimated",
    f"INSERT INTO {SCHEMA}.misestimated SELECT n FROM generate_series(1, 100050) AS n",
    # 100,000 rows of about 1,000 characters each: more than 100 MB once loaded, to spill.
    f"CREATE TABLE {SCHEMA}.wide (id integer, text text)",
    f"INSERT INTO {SCHEMA}.wide SELECT n, n || repeat(md5(n::text), 31) FROM generate_series(1, 100000) AS n",
    f"ANALYZE {SCHEMA}.wide",
    # Read in about 2 s, so a run on it is still going when a test looks.
    f"CREATE VIEW {SCHEMA}.slow AS SELECT 1 AS n FROM pg_sleep(2)",
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


def test_after_a_restart_a_run_waits_for_the_password(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-05: a run reads the database, so without a credential store it waits
    # for the password, as the source asks for it.
    source_id = chosen_source(start_app(None), "people")
    after = start_app(None)

    response = after.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})

    assert (response.status_code, response.json()) == (
        409,
        {"detail": "Enter the password for this database first."},
    )


def test_a_password_the_server_refuses_when_a_run_starts_asks_for_it_again(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-06: the run's start is the read that finds the stored password refused.
    store, source = saved_with_a_wrong_stored_password(start_app, workspace)
    after = start_app(store)
    listed = objects_in(after.get(f"/api/sources/{source.id}/objects").json(), SCHEMA)
    after.put(f"/api/sources/{source.id}/selection", json={"object_ids": [listed["people"]["id"]]})

    response = after.post("/api/profile-runs", json={"source_id": source.id, "mode": "fast"})

    assert (response.status_code, response.json()) == (422, REFUSED)
    (again,) = after.get("/api/sources").json()
    assert again["password_needed"] is True
    assert after.get("/api/profile-runs").json() == []


def test_a_run_on_an_object_changed_since_the_list_is_refused_pointing_to_the_refresh(
    client: TestClient, postgres_admin: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-13: "When a run starts on such an object, it is refused with a message
    # pointing to the refresh."
    source_id = chosen_source(client, "people", "changing")
    postgres_admin(f"ALTER TABLE {SCHEMA}.changing ADD COLUMN note text")

    response = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})

    assert (response.status_code, response.json()) == (
        409,
        {
            "detail": f"{SCHEMA}.changing changed since the list was refreshed."
            " Refresh the list, then profile again."
        },
    )
    assert client.get("/api/profile-runs").json() == []


def ended(client: TestClient, run_id: str) -> dict:
    """The run once it has ended, from the list, as a test mustn't close a workspace mid-run."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        (run,) = [run for run in client.get("/api/profile-runs").json() if run["id"] == run_id]
        if run["status"] != "running":
            return run
        time.sleep(0.01)
    raise AssertionError("The run didn't end.")


def test_a_fast_profile_reads_each_chosen_object_as_planned_with_the_runs_seed(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-18: one run over every chosen object; a small table is read whole and a
    # view from its first rows, and the seed is saved in the run.
    client = start_app(None)
    source_id = chosen_source(client, "people", "names")

    response = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})

    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert ended(client, run_id)["status"] == "completed"
    run = read_profile_run(workspace.engine, run_id)
    assert (run.mode, run.seed is not None) == ("fast", True)
    assert run.scope["source_id"] == source_id
    assert [(each["name"], each["method"]) for each in run.scope["objects"]] == [
        ("names", "view_first_rows"),
        ("people", "whole"),
    ]


def saved(workspace: Workspace, source_id: str, run_id: str) -> dict[tuple, tuple]:
    """What a run saved, by object name, column (None for the object) and metric: value, scope."""
    names = {}
    for found in read_chosen_objects(workspace.engine, source_id):
        names[found.dataset.id] = found.name
        names.update({field.id: field.name for field in found.dataset.fields})
    return {
        (names[seen.dataset_id], names.get(seen.field_id), seen.metric): (seen.value, seen.scope)
        for seen in read_observations(workspace.engine, run_id)
    }


def test_each_objects_columns_get_the_metrics_for_their_type_and_only_null_is_missing(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-24 and the Assumptions' metrics: counts for every column, text lengths
    # and masked patterns for text columns, never "values that don't fit"; only NULL is missing
    # (agreed for DRIVN-51). A whole table is measured "full", a view's first rows "sample".
    client = start_app(None)
    source_id = chosen_source(client, "people", "names")
    run_id = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "fast"}
    ).json()["id"]
    assert ended(client, run_id)["status"] == "completed"

    name = {
        "missing_count": 1,
        "missing_percent": 33.33,
        "distinct_count": 2,
        "distinct_percent": 100.0,
        "shortest_length": 0,
        "longest_length": 3,
        "average_length": 1.5,
        "masked_patterns": [
            {"pattern": "", "count": 1, "cut": False},
            {"pattern": "AAA", "count": 1, "cut": False},
        ],
    }
    counts = {"missing_count": 0, "missing_percent": 0.0, "distinct_count": 3, "distinct_percent": 100.0}
    json = {"missing_count": 1, "missing_percent": 33.33, "distinct_count": 2, "distinct_percent": 100.0}
    assert saved(workspace, source_id, run_id) == {
        ("people", None, "rows_read"): (3, "full"),
        **{("people", "id", metric): (value, "full") for metric, value in counts.items()},
        **{("people", "name", metric): (value, "full") for metric, value in name.items()},
        **{("people", "notes", metric): (value, "full") for metric, value in json.items()},
        ("names", None, "rows_read"): (3, "sample"),
        **{("names", "name", metric): (value, "sample") for metric, value in name.items()},
    }
    assert read_findings(workspace.engine, run_id) == []


def test_a_run_that_cant_read_an_object_fails_and_says_which(
    start_app: StartApp, workspace: Workspace, postgres_admin: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-18 and AC-22: the run reads each object, and one it can't read ends it
    # `failed`, with the object and DrivnX's reason, never DuckDB's or the server's text. Its
    # columns are as listed, so the start doesn't refuse it.
    client = start_app(None)
    source_id = chosen_source(client, "people", "revoked")
    postgres_admin(f"REVOKE SELECT ON {SCHEMA}.revoked FROM drivnx_fixture")
    try:
        run_id = client.post(
            "/api/profile-runs", json={"source_id": source_id, "mode": "fast"}
        ).json()["id"]
        run = ended(client, run_id)
    finally:
        postgres_admin(f"GRANT SELECT ON {SCHEMA}.revoked TO drivnx_fixture")

    assert (run["status"], run["failure"]) == (
        "failed",
        f"{SCHEMA}.revoked: This connection's user isn't allowed to read this object any more."
        " Ask the database's administrator for SELECT on it, or leave it out.",
    )
    assert read_observations(workspace.engine, run_id) == []


def test_a_run_whose_connection_is_refused_says_why_without_naming_an_object(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-18: the job connects again after the start's check, and the server can
    # refuse it then, as when the password changed meanwhile. No object was being read, so the
    # reason stands alone. Started directly, as a request can't reach that moment.
    client = start_app(None)
    source_id = chosen_source(client, "people")
    source = find_source(workspace.engine, source_id)
    assert isinstance(source, StoredPostgresSource)
    chosen = read_chosen_objects(workspace.engine, source_id)

    run_id = profile_jobs.start_database_profile(
        workspace,
        "fast",
        details_of(source),
        source,
        "not-the-password",  # pragma: allowlist secret
        chosen,
        {found.dataset.id: reading_for(found.kind, 3) for found in chosen},
        # What the fixture's role could change in them: nothing.
        write_access={
            "role": "drivnx_fixture",
            "superuser": False,
            "objects": [],
            "chosen": [f"{SCHEMA}.people"],
        },
    )
    run = ended(client, run_id)

    assert (run["status"], run["failure"]) == (
        "failed",
        "The server refused this user name and password.",
    )


def test_a_second_run_is_refused_while_one_is_in_progress(client: TestClient) -> None:
    # CFX-APP-postgres/AC-17: "a second run is refused with a clear message".
    source_id = chosen_source(client, "slow")
    first = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    assert first.status_code == 202, first.text

    second = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})

    assert (second.status_code, second.json()) == (
        409,
        {"detail": "A profile is already running. Wait for it to finish, then try again."},
    )
    assert ended(client, first.json()["id"])["status"] == "completed"


def test_a_second_run_is_refused_without_connecting_to_the_server(
    client: TestClient, postgres_admin: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-17 and AC-23: refusing a second run needs nothing from the server, so
    # it opens no second session there. The role is held to the one session the job has.
    source_id = chosen_source(client, "slow")
    run_id = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "fast"}
    ).json()["id"]
    deadline = time.monotonic() + 30
    while not running_on(client, run_id) and time.monotonic() < deadline:
        time.sleep(0.01)
    postgres_admin(f"ALTER ROLE {postgres_fixture.USER} CONNECTION LIMIT 1")
    try:
        second = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    finally:
        postgres_admin(f"ALTER ROLE {postgres_fixture.USER} CONNECTION LIMIT -1")

    assert (second.status_code, second.json()) == (
        409,
        {"detail": "A profile is already running. Wait for it to finish, then try again."},
    )
    assert ended(client, run_id)["status"] == "completed"


def running_on(client: TestClient, run_id: str) -> str | None:
    """The object the run is reading, once its job has connected."""
    (run,) = [run for run in client.get("/api/profile-runs").json() if run["id"] == run_id]
    return run.get("current_object")


def test_a_running_run_says_which_object_it_is_on(client: TestClient) -> None:
    # CFX-APP-postgres/AC-17: "the header's profile status and the Profile tab show which object
    # a running run is on", from the run as the API lists it.
    source_id = chosen_source(client, "slow")
    run_id = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "fast"}
    ).json()["id"]

    deadline = time.monotonic() + 30
    seen = None
    while seen is None and time.monotonic() < deadline:
        (run,) = client.get("/api/profile-runs").json()
        seen = run.get("current_object")
        if run["status"] != "running":
            break
        time.sleep(0.01)

    assert seen == f"{SCHEMA}.slow"
    assert ended(client, run_id)["current_object"] is None


def test_a_database_runs_results_say_how_each_object_was_read(client: TestClient) -> None:
    # CFX-APP-postgres/AC-24: each metric's object and scope, and each object's method, for the
    # Profile tab's labels.
    source_id = chosen_source(client, "people", "names")
    run_id = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "fast"}
    ).json()["id"]
    assert ended(client, run_id)["status"] == "completed"

    response = client.get(f"/api/profile-runs/{run_id}")

    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["objects"] == [
        {"object": f"{SCHEMA}.names", "method": "view_first_rows", "stopped_at_time_limit": False},
        {"object": f"{SCHEMA}.people", "method": "whole", "stopped_at_time_limit": False},
    ]
    assert detail["file_changed"] is None
    assert {
        "object": f"{SCHEMA}.people",
        "column": "name",
        "metric": "missing_count",
        "value": 1,
        "scope": "full",
        "precision": "exact",
    } in detail["metrics"]
    assert {
        "object": f"{SCHEMA}.names",
        "column": None,
        "metric": "rows_read",
        "value": 3,
        "scope": "sample",
        "precision": "exact",
    } in detail["metrics"]


def test_an_object_larger_than_its_estimate_is_labelled_as_its_first_rows(
    client: TestClient,
) -> None:
    # CFX-APP-postgres/AC-18: "a smaller or never-analysed one whole, unless it returns more than
    # 100,000 rows, when it's labelled as the first rows returned, not random".
    source_id = chosen_source(client, "misestimated")
    run_id = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "fast"}
    ).json()["id"]
    assert ended(client, run_id)["status"] == "completed"

    detail = client.get(f"/api/profile-runs/{run_id}").json()

    assert detail["objects"] == [
        {"object": f"{SCHEMA}.misestimated", "method": "first_rows", "stopped_at_time_limit": False}
    ]
    (rows_read,) = [metric for metric in detail["metrics"] if metric["metric"] == "rows_read"]
    assert (rows_read["value"], rows_read["scope"]) == (100_000, "sample")


def test_a_database_runs_values_on_request_arent_available_yet(client: TestClient) -> None:
    # CFX-APP-postgres AC-28 comes with DRIVN-56.
    source_id = chosen_source(client, "people")
    run_id = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "fast"}
    ).json()["id"]
    assert ended(client, run_id)["status"] == "completed"

    response = client.get(f"/api/profile-runs/{run_id}/values", params={"column": "name"})

    assert (response.status_code, response.json()) == (
        409,
        {"detail": "Values on request for a database aren't available yet."},
    )


# DrivnX's sessions on the server, as its superuser sees them.
SESSIONS = (
    "SELECT * FROM postgres_query('admin', 'SELECT count(*) FROM pg_stat_activity"
    " WHERE application_name = ''DrivnX''')"
)


@pytest.fixture
def drivnx_sessions(tmp_path: Path) -> Iterator[Callable[[], int]]:
    """How many sessions DrivnX has open on the fixture's server now."""
    admin = postgres_fixture.dsn(postgres_fixture.SUPERUSER, postgres_fixture.SUPERUSER_PASSWORD)
    with duckdb_connection(tmp_path) as connection:
        load_postgres(connection)
        connection.execute(f"ATTACH '{admin}' AS admin (TYPE postgres)")
        yield lambda: connection.execute(SESSIONS).fetchone()[0]


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_a_run_holds_at_most_one_connection_to_the_server(
    client: TestClient, drivnx_sessions: Callable[[], int], mode: str
) -> None:
    # CFX-APP-postgres/AC-23: "DrivnX reads the server over at most one connection for it, on a
    # full profile too". Tables large enough that DuckDB's own scanner would read them over
    # several (ADR 0005), `wide` at about 14,000 pages, and a view slow enough to watch.
    source_id = chosen_source(client, "misestimated", "wide", "slow")
    seen: list[int] = []
    watching = threading.Event()

    def watch() -> None:
        while not watching.is_set():
            seen.append(drivnx_sessions())
            time.sleep(0.01)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        run_id = client.post(
            "/api/profile-runs", json={"source_id": source_id, "mode": mode}
        ).json()["id"]
        run = ended(client, run_id)
    finally:
        watching.set()
        watcher.join()

    assert run["status"] == "completed"
    assert max(seen) == 1, seen


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_a_run_creates_nothing_in_the_current_directory_and_spills_inside_the_workspace(
    client: TestClient,
    workspace_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    # CFX-APP-postgres/AC-23: "it creates nothing in the current directory, any spill files are
    # inside <workspace>/duckdb-tmp/", on a full profile too. A memory limit small enough that
    # the rows spill.
    current = tmp_path / "current"
    current.mkdir()
    monkeypatch.chdir(current)
    source_id = chosen_source(client, "wide")
    seen: dict[str, list[Path]] = {}
    measure = profile_jobs.measure_rows

    def watching_measure(*args, **kwargs):
        seen["spilled"] = list((workspace_dir / "duckdb-tmp").rglob("*"))
        return measure(*args, **kwargs)

    monkeypatch.setattr(profile_jobs, "measure_rows", watching_measure)
    monkeypatch.setattr(profile_jobs, "PROFILE_MEMORY_LIMIT", "100MB")

    run_id = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": mode}
    ).json()["id"]

    assert ended(client, run_id)["status"] == "completed"
    assert seen["spilled"] != []
    assert list(current.iterdir()) == []
    assert list((workspace_dir / "duckdb-tmp").iterdir()) == []
