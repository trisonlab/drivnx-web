"""The CSV profile's findings for each object of a database run, CFX-APP-postgres AC-26 and AC-27
(DRIVN-55)."""

import threading
from collections.abc import Callable, Iterator

import pytest
from artifacts.profiles import read_findings, read_observations
from artifacts.sources import read_chosen_objects
from artifacts.workspace import Workspace
from fastapi.testclient import TestClient
from local_api import profile_jobs

from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_password_store import (  # noqa: F401 (the fixtures)
    StartApp,
    start_app,
    workspace,
)
from .test_postgres_profile_api import ended

SCHEMA = "drivnx_findings"
WAIT_SECONDS = 30
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    # Both columns hold 10 distinct values; only `id` is declared a key.
    f"CREATE TABLE {SCHEMA}.keyed (id integer PRIMARY KEY, code text)",
    f"INSERT INTO {SCHEMA}.keyed SELECT n, 'C-' || n FROM generate_series(1, 10) AS n",
    # Both columns hold 10 distinct values; only `code` is declared unique.
    f"CREATE TABLE {SCHEMA}.unique_code (code text UNIQUE, label text)",
    f"INSERT INTO {SCHEMA}.unique_code SELECT 'C-' || n, 'L-' || n"
    " FROM generate_series(1, 10) AS n",
    # `a` holds 10 distinct values, `b` two, and they're one primary key together.
    f"CREATE TABLE {SCHEMA}.pairs (a integer, b integer, PRIMARY KEY (a, b))",
    f"INSERT INTO {SCHEMA}.pairs SELECT n, n % 2 FROM generate_series(1, 10) AS n",
    # 10 rows. `note` is missing in 3 and has 2 values in the rest; `status` has 1.
    f"CREATE TABLE {SCHEMA}.gaps (id integer PRIMARY KEY, note text, status text)",
    f"INSERT INTO {SCHEMA}.gaps SELECT n, CASE WHEN n > 3 THEN 'N-' || n % 2 END, 'open'"
    " FROM generate_series(1, 10) AS n",
    # 10 rows, each column with 2 values, so none is a possible identifier. `email` matches by
    # its name and its values; each other column by its name or by its values alone.
    f"CREATE TABLE {SCHEMA}.contacts (id integer PRIMARY KEY, email text, full_name text,"
    " reached_by text, reached_on text, payload text)",
    f"INSERT INTO {SCHEMA}.contacts SELECT n, 'a' || n % 2 || '@example.com',"
    " CASE WHEN n % 2 = 0 THEN 'Ann' ELSE 'Bob' END, 'b' || n % 2 || '@example.org',"
    " '+44 20 7946 000' || n % 2, '4111 1111 1111 111' || n % 2"
    " FROM generate_series(1, 10) AS n",
    # 10 rows with 5 values, so 5 copy an earlier row.
    f"CREATE TABLE {SCHEMA}.copies (code text)",
    f"INSERT INTO {SCHEMA}.copies SELECT 'C-' || n % 5 FROM generate_series(1, 10) AS n",
    # Its first 100,000 rows at once, so its fast reading is quick, and the 100,001st after 30 s,
    # so a full profile stops it at a time limit shorter than that, as in the stopping tests.
    f"CREATE VIEW {SCHEMA}.late_gaps AS SELECT n FROM generate_series(1, 100001) AS n"
    " WHERE n <= 100000 OR (SELECT true FROM pg_sleep(30))",
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


def run(client: TestClient, source_id: str, mode: str = "fast") -> str:
    response = client.post("/api/profile-runs", json={"source_id": source_id, "mode": mode})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def found(workspace: Workspace, source_id: str, run_id: str) -> list[tuple[str, str | None, str]]:
    """A run's findings in their saved order, each as (object, column or None, kind), the column
    being the one its first cited observation is of."""
    names = {}
    for chosen in read_chosen_objects(workspace.engine, source_id):
        names[chosen.dataset.id] = chosen.name
        names.update({field.id: field.name for field in chosen.dataset.fields})
    cited = {seen.id: seen for seen in read_observations(workspace.engine, run_id)}
    return [
        (
            names[cited[finding.observation_ids[0]].dataset_id],
            names.get(cited[finding.observation_ids[0]].field_id),
            finding.kind,
        )
        for finding in read_findings(workspace.engine, run_id)
    ]


def test_a_column_that_is_its_primary_key_gets_no_possible_identifier_finding(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-26: "a column with a declared primary key or unique constraint gets no
    # possible-identifier finding". `code` is as distinct, and declared nothing.
    client = start_app(None)
    source_id = chosen_source(client, "keyed")

    run_id = run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    assert identifiers(workspace, source_id, run_id) == ["code"]


def identifiers(workspace: Workspace, source_id: str, run_id: str) -> list[str | None]:
    """The columns a run found possible identifiers, in its order."""
    return [
        column
        for _, column, kind in found(workspace, source_id, run_id)
        if kind == "possible_identifier"
    ]


def test_a_column_that_is_its_unique_constraint_gets_no_possible_identifier_finding(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-26: "a column with a declared primary key or unique constraint gets no
    # possible-identifier finding". `label` is as distinct, and declared nothing.
    client = start_app(None)
    source_id = chosen_source(client, "unique_code")

    run_id = run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    assert identifiers(workspace, source_id, run_id) == ["label"]


def test_a_column_of_a_composite_key_can_be_a_possible_identifier(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-26, as agreed for DRIVN-55: only a key of the column alone says it
    # identifies each row, so a column of a composite key can be a possible identifier.
    client = start_app(None)
    source_id = chosen_source(client, "pairs")

    run_id = run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    assert identifiers(workspace, source_id, run_id) == ["a"]


def test_a_run_finds_significant_missing_values_and_a_single_value_column(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-26: the CSV profile's findings, in its fixed order by kind. 3 of 10
    # missing is at least the CSV profile's 20%.
    client = start_app(None)
    source_id = chosen_source(client, "gaps")

    run_id = run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    assert found(workspace, source_id, run_id) == [
        ("gaps", "note", "missing_values"),
        ("gaps", "status", "single_value"),
    ]


def test_each_sensitive_field_rule_finds_a_possibly_sensitive_field(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-27: "columns matching the sensitive-field name rules or the email,
    # phone or payment card patterns ... each gets a possibly-sensitive finding", saying which.
    client = start_app(None)
    source_id = chosen_source(client, "contacts")

    run_id = run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    assert found(workspace, source_id, run_id) == [
        ("contacts", column, "possibly_sensitive")
        for column in ("email", "full_name", "reached_by", "reached_on", "payload")
    ]
    said = "Possibly sensitive field: its "
    assert [finding.interpretation for finding in read_findings(workspace.engine, run_id)] == [
        f"{said}name suggests an email address, and its values are shaped like email addresses",
        f"{said}name suggests a person's name",
        f"{said}values are shaped like email addresses",
        f"{said}values are shaped like phone numbers",
        f"{said}values are shaped like payment card numbers",
    ]


@pytest.mark.parametrize(
    ("mode", "expected"), [("fast", []), ("full", [("copies", None, "duplicate_rows")])]
)
def test_only_a_full_profile_finds_rows_that_copy_another(
    start_app: StartApp, workspace: Workspace, postgres_server: str, mode: str, expected: list
) -> None:
    # CFX-APP-postgres/AC-26: "and on a full profile duplicate rows", as only a full profile
    # counts them (DRIVN-53).
    client = start_app(None)
    source_id = chosen_source(client, "copies")

    run_id = run(client, source_id, mode)

    assert ended(client, run_id)["status"] == "completed"
    assert found(workspace, source_id, run_id) == expected


def test_an_object_stopped_at_its_time_limit_has_no_findings_and_the_others_do(
    start_app: StartApp,
    workspace: Workspace,
    postgres_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CFX-APP-postgres/AC-20 and AC-26: an object stopped at its time limit has no results, so
    # nothing to find from, and the run goes on to the next. A minute lasts a quarter of a second.
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", 0.25)
    client = start_app(None)
    source_id = chosen_source(client, "gaps", "late_gaps")

    response = client.post(
        "/api/profile-runs",
        json={"source_id": source_id, "mode": "full", "time_limit_minutes": 1},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    assert ended(client, run_id)["status"] == "completed"
    (late,) = [c for c in read_chosen_objects(workspace.engine, source_id) if c.name == "late_gaps"]
    assert [
        seen.metric
        for seen in read_observations(workspace.engine, run_id)
        if seen.dataset_id == late.dataset.id
    ] == [profile_jobs.TIME_LIMIT_REACHED]
    assert found(workspace, source_id, run_id) == [
        ("gaps", "note", "missing_values"),
        ("gaps", "status", "single_value"),
    ]


def test_a_cancel_after_findings_are_worked_out_still_saves_none(
    start_app: StartApp,
    workspace: Workspace,
    postgres_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CFX-APP-postgres/AC-26, as a CSV's (CFX-APP-csv-profile/AC-26): the run has its findings
    # ready, but ends as `cancelled`, and a cancelled run has none.
    ready, cancelled = threading.Event(), threading.Event()
    findings_for = profile_jobs.findings_for

    def findings_then_cancel(observations, names, **keys):
        found = findings_for(observations, names, **keys)
        ready.set()
        assert cancelled.wait(WAIT_SECONDS)
        return found

    monkeypatch.setattr(profile_jobs, "findings_for", findings_then_cancel)
    client = start_app(None)
    source_id = chosen_source(client, "gaps")
    run_id = run(client, source_id)
    assert ready.wait(WAIT_SECONDS)
    assert client.post(f"/api/profile-runs/{run_id}/cancel").status_code == 202
    cancelled.set()

    assert ended(client, run_id)["status"] == "cancelled"
    assert read_findings(workspace.engine, run_id) == []


def test_a_runs_findings_are_by_object_in_its_order_then_by_kind(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-26, as agreed for DRIVN-55: by object in the run's order (by schema
    # then name), each object's in the fixed order by kind, so not by kind across objects.
    client = start_app(None)
    source_id = chosen_source(client, "keyed", "gaps")

    run_id = run(client, source_id)

    assert ended(client, run_id)["status"] == "completed"
    assert found(workspace, source_id, run_id) == [
        ("gaps", "note", "missing_values"),
        ("gaps", "status", "single_value"),
        ("keyed", "code", "possible_identifier"),
    ]
