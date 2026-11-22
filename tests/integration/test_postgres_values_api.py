"""`GET /api/profile-runs/{run_id}/values` for a database's run (DRIVN-56, DRIVN-45 AC-27 and
AC-28)."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import duckdb
import httpx
import pytest
from artifacts.profiles import read_profile_run
from artifacts.sources import find_source
from artifacts.workspace import Workspace
from fastapi.testclient import TestClient
from local_api import profile_jobs, profile_runs_api
from local_api.credentials import entry

from .test_credential_store import MemoryStore
from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_password_store import (  # noqa: F401 (the fixtures)
    REFUSED,
    WRONG,
    StartApp,
    start_app,
    workspace,
)
from .test_postgres_profile_api import ended
from .test_postgres_values import on_server  # noqa: F401 (the fixture)

SCHEMA = "drivnx_values_api"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.people (id integer PRIMARY KEY, city text)",
    f"INSERT INTO {SCHEMA}.people VALUES (1, 'Oslo'), (2, 'Oslo'), (3, 'Bergen')",
    f"ANALYZE {SCHEMA}.people",
    f"CREATE VIEW {SCHEMA}.cities AS SELECT city FROM {SCHEMA}.people",
    # Larger than a fast profile's sample, so it's sampled by page.
    f"CREATE TABLE {SCHEMA}.big (id integer) WITH (autovacuum_enabled = false)",
    f"INSERT INTO {SCHEMA}.big SELECT n FROM generate_series(1, 150000) AS n",
    f"ANALYZE {SCHEMA}.big",
    # `email` is possibly sensitive by its name and its values (DRIVN-55).
    f"CREATE TABLE {SCHEMA}.contacts (id integer PRIMARY KEY, email text)",
    f"INSERT INTO {SCHEMA}.contacts SELECT n, 'a' || n % 2 || '@example.com'"
    " FROM generate_series(1, 10) AS n",
    # Only the test that changes it chooses it.
    f"CREATE TABLE {SCHEMA}.changing (id integer)",
    f"CREATE TABLE {SCHEMA}.notes (body text)",
    f"INSERT INTO {SCHEMA}.notes VALUES (repeat('a', 1000)), (repeat('b', 1001))",
    # Its rows come only after the sleep, longer than any time limit here.
    f"CREATE VIEW {SCHEMA}.sleepy AS SELECT n FROM generate_series(1, 2) AS n"
    " WHERE (SELECT true FROM pg_sleep(20))",
    f"CREATE VIEW {SCHEMA}.three_seconds AS SELECT 1 AS n FROM pg_sleep(3)",
    # Read in about 2 s, so a run on it can be cancelled.
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


def completed(client: TestClient, source_id: str, mode: str = "fast", **body: object) -> str:
    """A run of the source's chosen objects, once it has completed."""
    started = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": mode, **body}
    )
    assert started.status_code == 202, started.text
    run = ended(client, started.json()["id"])
    assert run["status"] == "completed", run
    return run["id"]


def values_of(
    client: TestClient, run_id: str, name: str, column: str, **params: str
) -> httpx.Response:
    return client.get(
        f"/api/profile-runs/{run_id}/values",
        params={"schema": SCHEMA, "object": name, "column": column, **params},
    )


def cell(value: str, cut: bool = False) -> dict:
    return {"value": value, "cut": cut}


def test_a_columns_values_are_read_from_the_database_labelled_with_how_and_when(
    client: TestClient,
) -> None:
    # DRIVN-45 AC-28: "computed by querying the database with that run's method and seed,
    # labelled with that scope, the time computed".
    run_id = completed(client, chosen_source(client, "people"))
    before = datetime.now(UTC)

    response = values_of(client, run_id, "people", "city")

    assert response.status_code == 200, response.text
    found = response.json()
    assert found["profile_run_id"] == run_id
    assert (found["object"], found["kind"], found["column"]) == (
        f"{SCHEMA}.people",
        "table",
        "city",
    )
    assert (found["method"], found["scope"], found["rows_read"]) == ("whole", "full", 3)
    assert datetime.fromisoformat(found["computed_at"]) >= before
    assert found["frequent"] == [
        {"value": cell("Oslo"), "count": 2},
        {"value": cell("Bergen"), "count": 1},
    ]


def test_a_page_sampled_objects_values_are_from_the_pages_its_runs_seed_picks(
    start_app: StartApp, workspace: Workspace, on_server: Callable[[str], tuple]
) -> None:
    # DRIVN-45 AC-28: "with that run's method and seed". The server's sample at the percent and
    # seed the run saved says which rows those are.
    client = start_app(None)
    run_id = completed(client, chosen_source(client, "big"))
    saved = read_profile_run(workspace.engine, run_id)
    (planned,) = saved.scope["objects"]
    (rows, highest) = on_server(
        f"SELECT count(*), max(id)::text FROM {SCHEMA}.big"
        f" TABLESAMPLE SYSTEM ({planned['percent']}) REPEATABLE ({saved.seed})"
    )

    found = values_of(client, run_id, "big", "id").json()

    assert (found["method"], found["scope"]) == ("page_sample", "sample")
    assert (found["rows_read"], found["maximum"]) == (rows, cell(highest))
    assert rows < 150_000


def test_a_full_runs_values_are_from_every_row(client: TestClient) -> None:
    # DRIVN-45 AC-28 with a full run's method (decision A).
    run_id = completed(client, chosen_source(client, "people"), "full")

    found = values_of(client, run_id, "people", "city").json()

    assert (found["method"], found["scope"], found["rows_read"]) == ("full", "full", 3)


def test_a_views_values_say_it_is_a_view(client: TestClient) -> None:
    # DRIVN-45 AC-28: "for a view, that the rows may differ", which the screen says by its kind.
    run_id = completed(client, chosen_source(client, "cities"))

    found = values_of(client, run_id, "cities", "city").json()

    assert (found["kind"], found["method"], found["scope"]) == (
        "view",
        "view_first_rows",
        "sample",
    )


def test_values_are_cut_at_1000_characters_and_never_kept_by_the_browser(
    client: TestClient,
) -> None:
    # DRIVN-45 AC-28: "cut at 1,000 characters, and sent with `Cache-Control: no-store`".
    run_id = completed(client, chosen_source(client, "notes"))

    response = values_of(client, run_id, "notes", "body")

    assert response.headers["cache-control"] == "no-store"
    found = response.json()
    assert (found["minimum"], found["maximum"]) == (
        cell("a" * 1000),
        cell("b" * 1000, cut=True),
    )


NOT_COMPLETED = "This profile hasn't completed, so it has no values to show."


def test_values_of_a_run_that_didnt_complete_are_refused(client: TestClient) -> None:
    source_id = chosen_source(client, "slow")
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    run_id = started.json()["id"]
    assert client.post(f"/api/profile-runs/{run_id}/cancel").status_code == 202
    assert ended(client, run_id)["status"] == "cancelled"

    response = values_of(client, run_id, "slow", "n")

    assert (response.status_code, response.json()) == (409, {"detail": NOT_COMPLETED})


NO_SUCH_OBJECT = "There's no such object in this profile."


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"schema": SCHEMA, "object": "missing", "column": "city"}, NO_SUCH_OBJECT),
        ({"schema": "public", "object": "people", "column": "city"}, NO_SUCH_OBJECT),
        ({"column": "city"}, NO_SUCH_OBJECT),
        (
            {"schema": SCHEMA, "object": "people", "column": "missing"},
            "There's no such column in this profile.",
        ),
    ],
)
def test_values_of_an_object_or_column_the_run_didnt_read_are_not_found(
    client: TestClient, params: dict, detail: str
) -> None:
    run_id = completed(client, chosen_source(client, "people"))

    response = client.get(f"/api/profile-runs/{run_id}/values", params=params)

    assert (response.status_code, response.json()) == (404, {"detail": detail})


def test_values_of_an_object_changed_since_the_run_are_refused_and_the_others_still_answer(
    client: TestClient, postgres_admin: Callable[[str], None]
) -> None:
    # Decision B: the run's rows can't be read again from an object that changed since, which
    # DrivnX knows from the refresh (DRIVN-57).
    source_id = chosen_source(client, "changing", "people")
    run_id = completed(client, source_id)
    postgres_admin(f"ALTER TABLE {SCHEMA}.changing ADD COLUMN note text")
    assert client.post(f"/api/sources/{source_id}/discover").status_code == 200

    refused = values_of(client, run_id, "changing", "id")

    assert (refused.status_code, refused.json()) == (
        409,
        {
            "detail": f"{SCHEMA}.changing changed since this run."
            " Profile it again to see its values."
        },
    )
    assert values_of(client, run_id, "people", "city").status_code == 200


def test_values_of_an_object_no_longer_chosen_are_refused(client: TestClient) -> None:
    # DRIVN-45 AC-11: "only chosen objects can be previewed or profiled", and so read again.
    source_id = chosen_source(client, "people")
    run_id = completed(client, source_id)
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    chose_again = client.put(
        f"/api/sources/{source_id}/selection", json={"object_ids": [listed["cities"]["id"]]}
    )
    assert chose_again.status_code == 200, chose_again.text

    response = values_of(client, run_id, "people", "city")

    assert (response.status_code, response.json()) == (
        409,
        {"detail": f"{SCHEMA}.people is no longer chosen. Choose it again to see its values."},
    )


def test_values_of_an_object_stopped_at_its_time_limit_are_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # It has no results (DRIVN-45 AC-20), so no rows to read again. A minute lasts a quarter of
    # a second here, as in the time limit's own test.
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", 0.25)
    run_id = completed(client, chosen_source(client, "sleepy"), "full", time_limit_minutes=1)

    response = values_of(client, run_id, "sleepy", "n")

    assert (response.status_code, response.json()) == (
        409,
        {
            "detail": f"{SCHEMA}.sleepy was stopped at this run's time limit,"
            " so it has no values to show."
        },
    )


POSSIBLY_SENSITIVE = (
    "This column may hold personal data. Confirm that you want to see its values."
)


def test_a_possibly_sensitive_columns_values_need_the_extra_confirmation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DRIVN-45 AC-27: "its values need the extra confirmation", as for a CSV (DRIVN-9 AC-29),
    # asked before the database is read.
    run_id = completed(client, chosen_source(client, "contacts"))
    reads: list[int] = []
    read = profile_runs_api.object_values_of

    def counted(*args: object, **kwargs: object) -> object:
        reads.append(1)
        return read(*args, **kwargs)

    monkeypatch.setattr(profile_runs_api, "object_values_of", counted)

    refused = values_of(client, run_id, "contacts", "email")
    read_before_confirming = len(reads)
    confirmed = values_of(client, run_id, "contacts", "email", confirmed="true")

    assert (refused.status_code, refused.json()) == (409, {"detail": POSSIBLY_SENSITIVE})
    assert read_before_confirming == 0
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["frequent"] == [
        {"value": cell("a0@example.com"), "count": 5},
        {"value": cell("a1@example.com"), "count": 5},
    ]
    assert values_of(client, run_id, "contacts", "id").status_code == 200


BUSY = "DrivnX is already reading a file. Wait for it to finish, then try again."


def test_values_while_a_profile_runs_are_refused(client: TestClient) -> None:
    # As a file's (ADR 0004): two DuckDB jobs could take twice the memory.
    # One source: the same connection's details are the same source.
    source_id = chosen_source(client, "people", "slow")
    run_id = completed(client, source_id)
    running = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    assert running.status_code == 202, running.text

    response = values_of(client, run_id, "people", "city")
    ended(client, running.json()["id"])

    assert (response.status_code, response.json()) == (409, {"detail": BUSY})


def test_values_from_a_database_that_cant_be_read_say_why_and_the_saved_metrics_still_show(
    start_app: StartApp, workspace: Workspace
) -> None:
    # DRIVN-45 AC-28: "Given the database unreachable, then a clear message shows, and the saved
    # metrics still show". Here the server refuses the stored password, as after it was changed.
    store = MemoryStore()
    client = start_app(store)
    source_id = chosen_source(client, "people")
    run_id = completed(client, source_id)
    metrics = client.get(f"/api/profile-runs/{run_id}").json()["metrics"]
    source = find_source(workspace.engine, source_id)
    assert source is not None
    store.passwords[entry(source)] = WRONG
    after = start_app(store)

    response = values_of(after, run_id, "people", "city")

    assert (response.status_code, response.json()) == (422, REFUSED)
    assert after.get(f"/api/profile-runs/{run_id}").json()["metrics"] == metrics
    assert metrics != []


def test_a_full_runs_values_that_take_longer_than_its_time_limit_are_refused_saying_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Decision A: "refused 'took longer than this run's time limit of N minutes'". The run reads
    # the view within a minute of 20 s; its values then have a minute of a quarter of a second.
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", 20)
    run_id = completed(
        client, chosen_source(client, "three_seconds"), "full", time_limit_minutes=1
    )
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", 0.25)

    response = values_of(client, run_id, "three_seconds", "n")

    assert (response.status_code, response.json()) == (
        422,
        {
            "detail": f"Reading {SCHEMA}.three_seconds took longer than this run's time limit"
            " of 1 minute."
        },
    )


def test_after_a_restart_values_wait_for_the_password(start_app: StartApp) -> None:
    # DRIVN-45 AC-05: with no credential store, the password lasts until DrivnX stops.
    client = start_app(None)
    run_id = completed(client, chosen_source(client, "people"))

    response = values_of(start_app(None), run_id, "people", "city")

    assert (response.status_code, response.json()) == (
        409,
        {"detail": "Enter the password for this database first."},
    )


# DuckDB's text can quote a value it read, so it must never travel (DRIVN-45 AC-29).
PLANTED = "Ann-7731-planted"


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (duckdb.OutOfMemoryException, "DrivnX ran out of memory working out these values."),
        (duckdb.ConversionException, "DrivnX couldn't work out these values."),
    ],
)
def test_values_duckdb_cant_work_out_say_why_in_drivnxs_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, error: type, detail: str
) -> None:
    # DRIVN-45 AC-28: "a clear message shows" when the values can't be had (found in review).
    run_id = completed(client, chosen_source(client, "people"))

    def failing(*_args: object, **_kwargs: object) -> None:
        raise error(f"Could not work out {PLANTED}")

    monkeypatch.setattr(profile_jobs, "values_for", failing)

    response = values_of(client, run_id, "people", "city")

    assert (response.status_code, response.json()) == (422, {"detail": detail})
    assert PLANTED not in response.text
