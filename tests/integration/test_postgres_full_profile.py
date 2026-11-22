"""A full profile of a database's chosen objects through the API, CFX-APP-postgres AC-19 and
ADR 0009 (DRIVN-53)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from artifacts.profiles import read_observations, read_profile_run
from artifacts.sources import read_chosen_objects
from artifacts.workspace import Workspace
from fastapi.testclient import TestClient

from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_password_store import (  # noqa: F401 (the fixtures)
    StartApp,
    start_app,
    workspace,
)
from .test_postgres_profile_api import ended

SCHEMA = "drivnx_full_profile"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role. `repeated` has more rows than a fast
# profile reads: 150,000, each value of n % 50,000 three times, so 50,000 distinct values and
# 100,000 rows that copy an earlier one. Its estimate is analysed, so a fast reading samples it.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.repeated (id integer, code text)",
    f"INSERT INTO {SCHEMA}.repeated SELECT n % 50000, 'C-' || (n % 50000)"
    " FROM generate_series(1, 150000) AS n",
    f"ANALYZE {SCHEMA}.repeated",
    # 150,000 rows in the order written: the first 100,000 codes `C-<n>`, the last 50,000
    # `#<n>`. A page sample from across the table has both shapes; its first rows only one.
    f"CREATE TABLE {SCHEMA}.ordered (code text) WITH (autovacuum_enabled = false)",
    f"INSERT INTO {SCHEMA}.ordered SELECT CASE WHEN n <= 100000 THEN 'C-' || n ELSE '#' || n END"
    " FROM generate_series(1, 150000) AS n",
    f"ANALYZE {SCHEMA}.ordered",
    # Its 150,000 rows, of which a fast reading takes the first 100,000.
    f"CREATE VIEW {SCHEMA}.ordered_view AS SELECT code FROM {SCHEMA}.ordered",
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


def full_run(client: TestClient, source_id: str, **extra: object) -> str:
    response = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "full", **extra}
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


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


def test_a_full_profile_reads_every_row_and_counts_them_exactly(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-19: "every row of each object is read, with exact results and each
    # object's exact row count".
    client = start_app(None)
    source_id = chosen_source(client, "repeated")

    run_id = full_run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    measured = saved(workspace, source_id, run_id)
    assert measured[("repeated", None, "rows_read")] == (150_000, "full")
    assert measured[("repeated", "id", "distinct_count")] == (50_000, "full")


def test_a_full_profile_counts_the_rows_of_a_table_with_no_columns(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-19: "each object's exact row count", for a table with no columns too
    # (found in review).
    client = start_app(None)
    source_id = chosen_source(client, "no_columns")

    run_id = full_run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    assert saved(workspace, source_id, run_id)[("no_columns", None, "rows_read")] == (2, "full")


def test_a_full_profile_counts_the_rows_that_copy_an_earlier_one(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-19, as a CSV's full profile counts them (decided for DRIVN-53): each
    # of the 50,000 rows appears 3 times, so 100,000 copy an earlier one.
    client = start_app(None)
    source_id = chosen_source(client, "repeated")

    run_id = full_run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    measured = saved(workspace, source_id, run_id)
    assert measured.get(("repeated", None, "duplicate_rows")) == (100_000, "full")


def test_a_full_profiles_masked_patterns_come_from_the_objects_sample(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres, the Assumptions: "masked patterns always come from the fast profile's
    # sample rows, even on a full profile, and are labelled that way" (decided for DRIVN-53).
    # A page sample reaches the last third of `ordered`, whose `#` codes its first rows don't.
    client = start_app(None)
    source_id = chosen_source(client, "ordered")

    run_id = full_run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    measured = saved(workspace, source_id, run_id)
    patterns, scope = measured[("ordered", "code", "masked_patterns")]
    assert scope == "sample"
    assert {found["pattern"][0] for found in patterns} == {"A", "#"}
    assert measured[("ordered", None, "rows_read")] == (150_000, "full")


@pytest.mark.parametrize(("asked", "kept"), [({}, 10), ({"time_limit_minutes": 25}, 25)])
def test_a_full_profile_keeps_its_time_limit_per_object_in_the_run(
    start_app: StartApp, workspace: Workspace, postgres_server: str, asked: dict, kept: int
) -> None:
    # CFX-APP-postgres, the Assumptions: "the per-table time limit can be set from 1 to 120
    # minutes for a run, and defaults to 10 minutes"; the run's scope records it (Low-Level Design).
    client = start_app(None)
    source_id = chosen_source(client, "repeated")

    run_id = full_run(client, source_id, **asked)

    assert ended(client, run_id)["status"] == "completed"
    assert read_profile_run(workspace.engine, run_id).scope.get("time_limit_minutes") == kept


@pytest.mark.parametrize("minutes", [0, 121])
def test_a_time_limit_outside_1_to_120_minutes_is_refused(
    start_app: StartApp, postgres_server: str, minutes: int
) -> None:
    # CFX-APP-postgres, the Assumptions: "from 1 to 120 minutes".
    client = start_app(None)
    source_id = chosen_source(client, "repeated")

    response = client.post(
        "/api/profile-runs",
        json={"source_id": source_id, "mode": "full", "time_limit_minutes": minutes},
    )

    assert response.status_code == 422, response.text
    assert client.get("/api/profile-runs").json() == []


TIME_LIMIT_NOT_HERE = {"detail": "A time limit applies only to a database's full profile."}


def test_a_time_limit_on_a_databases_fast_profile_is_refused(
    start_app: StartApp, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-20: the limit is a full profile's; a fast one reads at most a sample.
    client = start_app(None)
    source_id = chosen_source(client, "repeated")

    response = client.post(
        "/api/profile-runs",
        json={"source_id": source_id, "mode": "fast", "time_limit_minutes": 5},
    )

    assert (response.status_code, response.json()) == (422, TIME_LIMIT_NOT_HERE)
    assert client.get("/api/profile-runs").json() == []


def test_a_time_limit_on_a_files_profile_is_refused(client: TestClient, tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-20: a file's profiles have no time limit (CFX-APP-csv-profile).
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n", encoding="utf-8", newline="")
    source_id = client.post("/api/sources", json={"path": str(path)}).json()["id"]

    response = client.post(
        "/api/profile-runs",
        json={"source_id": source_id, "mode": "full", "time_limit_minutes": 5},
    )

    assert (response.status_code, response.json()) == (422, TIME_LIMIT_NOT_HERE)
    assert client.get("/api/profile-runs").json() == []


def test_a_full_runs_detail_says_each_object_was_read_whole(
    start_app: StartApp, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-19 and AC-24: the Profile tab labels each object by how it was read, and
    # a full profile reads every row of it, whatever its fast reading would be.
    client = start_app(None)
    source_id = chosen_source(client, "repeated")
    run_id = full_run(client, source_id)
    assert ended(client, run_id)["status"] == "completed"

    detail = client.get(f"/api/profile-runs/{run_id}").json()

    # Its patterns' rows, from a page sample, are another test's.
    (read,) = detail["objects"]
    assert {key: value for key, value in read.items() if key != "patterns_from"} == {
        "object": f"{SCHEMA}.repeated",
        "method": "full",
        "stopped_at_time_limit": False,
        "out_of_date": False,
    }
    assert {
        "object": f"{SCHEMA}.repeated",
        "column": None,
        "metric": "rows_read",
        "value": 150_000,
        "scope": "full",
        "precision": "exact",
    } in detail["metrics"]


def test_a_full_runs_detail_says_how_each_objects_patterns_were_read(
    start_app: StartApp, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-24 and the Assumptions: "masked patterns always come from the fast
    # profile's sample rows, even on a full profile, and are labelled that way": a view's from its
    # first rows, a table's from a page sample (found in review).
    client = start_app(None)
    source_id = chosen_source(client, "ordered", "ordered_view")
    run_id = full_run(client, source_id)
    assert ended(client, run_id)["status"] == "completed"

    objects = {
        found["object"]: found for found in client.get(f"/api/profile-runs/{run_id}").json()["objects"]
    }

    assert objects[f"{SCHEMA}.ordered_view"]["patterns_from"] == {
        "method": "view_first_rows",
        "rows": 100_000,
    }
    sample = objects[f"{SCHEMA}.ordered"]["patterns_from"]
    # 100,000 ÷ 150,000 of the pages, so about 100,000 rows.
    assert sample["method"] == "page_sample"
    assert 90_000 < sample["rows"] < 110_000, sample
