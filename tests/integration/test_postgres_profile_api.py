"""A fast profile of a database's chosen objects through the API, CFX-APP-postgres AC-13, AC-17,
AC-18 and AC-24 (DRIVN-51)."""

import time
from collections.abc import Callable, Iterator

import pytest
from artifacts.profiles import read_findings, read_observations, read_profile_run
from artifacts.sources import read_chosen_objects
from artifacts.workspace import Workspace
from fastapi.testclient import TestClient

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


def test_a_full_profile_of_a_database_isnt_available_yet(client: TestClient) -> None:
    # CFX-APP-postgres AC-19 comes with DRIVN-54; a fast profile is this ticket's.
    source_id = chosen_source(client, "people")

    response = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "full"})

    assert (response.status_code, response.json()) == (
        409,
        {"detail": "A full profile of a database isn't available yet."},
    )


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
    # CFX-APP-postgres/AC-18: the run reads each object, and one it can't read ends it `failed`,
    # with the object and DrivnX's reason, never DuckDB's or the server's text. Its columns are
    # as listed, so the start doesn't refuse it.
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
        f"{SCHEMA}.revoked: DrivnX couldn't read this object. It may have changed since it was"
        " listed: refresh the list.",
    )
    assert read_observations(workspace.engine, run_id) == []


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
