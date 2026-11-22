"""A database's earlier runs, what they describe, and restarts, CFX-APP-postgres AC-31, AC-32 and
AC-33 (DRIVN-57)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_profile_api import ended

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


def choose(client: TestClient, source_id: str, *names: str) -> None:
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    response = client.put(
        f"/api/sources/{source_id}/selection",
        json={"object_ids": [listed[name]["id"] for name in names]},
    )
    assert response.status_code == 200, response.text


def completed_run(client: TestClient, source_id: str, mode: str) -> dict:
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": mode})
    assert started.status_code == 202, started.text
    run = ended(client, started.json()["id"])
    assert run["status"] == "completed", run
    return run


def test_a_database_run_in_the_list_names_the_objects_it_read(client: TestClient) -> None:
    # CFX-APP-postgres/AC-31: "the user can list them with mode, status, time and objects".
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
    # CFX-APP-postgres/AC-31: "Given several runs of a source, then the user can list them with
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
    # CFX-APP-postgres/AC-31: only a database's run reads objects.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n", encoding="utf-8", newline="")
    source_id = client.post("/api/sources", json={"path": str(path)}).json()["id"]

    completed_run(client, source_id, "fast")

    (listed,) = client.get("/api/profile-runs").json()
    assert listed["object_names"] is None
