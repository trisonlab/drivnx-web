"""A database column's values, re-read as its run read the object (DRIVN-56, DRIVN-45 AC-28)."""

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from artifacts.sources import ChosenObject, DatasetField, StoredDataset
from artifacts.workspace import Workspace, open_workspace
from connectors.csv_files import duckdb_connection
from connectors import postgres
from connectors.postgres import (
    SAMPLE_ROWS,
    ConnectFailure,
    ConnectionDetails,
    Reading,
    load_postgres,
)
from local_api import profile_jobs
from profiling.first_rows import FIRST_ROWS
from profiling.on_request import Frequent

from .. import postgres_fixture
from .test_postgres_stopping import (  # noqa: F401 (the fixture)
    STOP_AT_THE_LIMIT_WITHIN_SECONDS,
    drivnx_queries,
    reading,
)

FIXTURE = ConnectionDetails(
    host=postgres_fixture.HOST,
    port=postgres_fixture.PORT,
    database=postgres_fixture.DATABASE,
    user=postgres_fixture.USER,
    tls_mode="prefer",
)
SCHEMA = "drivnx_values"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.small (id integer, label text)",
    f"INSERT INTO {SCHEMA}.small VALUES (1, 'one'), (2, 'one'), (3, 'two')",
    f"ANALYZE {SCHEMA}.small",
    # Blank strings, which a database keeps apart from NULL (agreed for DRIVN-51).
    f"CREATE TABLE {SCHEMA}.blanks (label text)",
    f"INSERT INTO {SCHEMA}.blanks VALUES (''), (''), (''), (' '), ('a'), (NULL)",
    # Larger than a fast profile's sample, so it's sampled by page. Autovacuum is off, so its
    # pages stay where the test put them.
    f"CREATE TABLE {SCHEMA}.big (id integer, label text) WITH (autovacuum_enabled = false)",
    f"INSERT INTO {SCHEMA}.big SELECT n, 'row ' || n FROM generate_series(1, 150000) AS n",
    f"ANALYZE {SCHEMA}.big",
    # Analysed empty, then filled past a sample, so a run reads it whole and finds it larger.
    f"CREATE TABLE {SCHEMA}.misestimated (id integer) WITH (autovacuum_enabled = false)",
    f"ANALYZE {SCHEMA}.misestimated",
    f"INSERT INTO {SCHEMA}.misestimated SELECT n FROM generate_series(1, 100050) AS n",
    # Its rows come only after the sleep, longer than any time limit here.
    f"CREATE VIEW {SCHEMA}.sleepy AS SELECT n FROM generate_series(1, 2) AS n"
    " WHERE (SELECT true FROM pg_sleep(20))",
    f"CREATE VIEW {SCHEMA}.three_seconds AS SELECT 1 AS n FROM pg_sleep(3)",
    f"CREATE VIEW {SCHEMA}.big_view AS SELECT id, label FROM {SCHEMA}.big",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO drivnx_fixture",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO drivnx_fixture",
)
# Each object's columns, as its dataset's fields.
ID_AND_LABEL = (("id", "int4", "integer"), ("label", "text", "text"))
COLUMNS = {
    "small": ID_AND_LABEL,
    "big": ID_AND_LABEL,
    "big_view": ID_AND_LABEL,
    "misestimated": (("id", "int4", "integer"),),
    "sleepy": (("n", "int4", "integer"),),
    "three_seconds": (("n", "int4", "integer"),),
    "blanks": (("label", "text", "text"),),
}


@pytest.fixture(scope="module", autouse=True)
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (*DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        for statement in DROP:
            postgres_admin(statement)


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Workspace]:
    opened = open_workspace(tmp_path / "workspace")
    yield opened
    opened.close()


@pytest.fixture
def on_server(tmp_path: Path) -> Iterator[Callable[[str], tuple]]:
    """One row of a query run on the fixture as its role, to compare DrivnX's values with."""
    with duckdb_connection(tmp_path / "on-server") as connection:
        load_postgres(connection)
        connection.execute(
            f"ATTACH '{postgres_fixture.dsn()}' AS fixture (TYPE postgres, READ_ONLY)"
        )

        def row(sql: str) -> tuple:
            query = "SELECT * FROM postgres_query('fixture', ?)"
            return connection.execute(query, [sql]).fetchone()

        yield row


def chosen(name: str, kind: str = "table") -> ChosenObject:
    """An object of this test's schema as a run chose it."""
    fields = tuple(
        DatasetField(f"{name}-{column}", column, physical, normalised)
        for column, physical, normalised in COLUMNS[name]
    )
    dataset = StoredDataset(f"dataset-{name}", "source", name, fields)
    return ChosenObject(dataset, SCHEMA, name, kind, ())


def test_a_fast_runs_values_of_an_object_it_read_whole_are_from_every_row(
    workspace: Workspace,
) -> None:
    # DRIVN-45 AC-28: "computed by querying the database with that run's method and seed".
    method, values = profile_jobs.object_values_of(
        workspace,
        FIXTURE,
        postgres_fixture.PASSWORD,
        chosen("small"),
        1,
        "fast",
        Reading("whole"),
        7,
    )

    assert method == "whole"
    assert (values.rows_read, values.whole_file) == (3, True)
    assert values.frequent == (Frequent("one", 2), Frequent("two", 1))
    assert (values.minimum, values.maximum) == ("one", "two")


def test_a_page_sampled_objects_values_are_from_the_pages_the_runs_seed_picks(
    workspace: Workspace, on_server: Callable[[str], tuple]
) -> None:
    # DRIVN-45 AC-28: "with that run's method and seed". The server's own sample at the run's
    # percent and seed says which rows those are.
    reading = Reading("page_sample", 10.0)
    sampled = on_server(
        f"SELECT count(*), min(id)::text, max(id)::text FROM {SCHEMA}.big"
        " TABLESAMPLE SYSTEM (10.0) REPEATABLE (7)"
    )

    asked = [
        profile_jobs.object_values_of(
            workspace, FIXTURE, postgres_fixture.PASSWORD, chosen("big"), 0, "fast", reading, 7
        )
        for _ in range(2)
    ]

    method, values = asked[0]
    assert method == "page_sample"
    assert (values.rows_read, values.minimum, values.maximum) == sampled
    assert values.rows_read < 150_000
    assert asked[1] == asked[0]


def test_a_views_values_are_from_its_first_rows(workspace: Workspace) -> None:
    # DRIVN-45 AC-28, for a view, which PostgreSQL won't sample by page (ADR 0005).
    method, values = profile_jobs.object_values_of(
        workspace,
        FIXTURE,
        postgres_fixture.PASSWORD,
        chosen("big_view", "view"),
        0,
        "fast",
        Reading("view_first_rows"),
        7,
    )

    assert method == "view_first_rows"
    assert (values.rows_read, values.whole_file) == (SAMPLE_ROWS, True)


def test_an_object_larger_than_its_estimate_gives_values_from_the_first_rows_its_run_measured(
    workspace: Workspace,
) -> None:
    # As its run measured it: read whole, found larger than a sample, so only its first rows
    # (ADR 0005), which `_measure_loaded` takes for `first_rows`.
    method, values = profile_jobs.object_values_of(
        workspace,
        FIXTURE,
        postgres_fixture.PASSWORD,
        chosen("misestimated"),
        0,
        "fast",
        Reading("whole"),
        7,
    )

    assert method == "first_rows"
    assert (values.rows_read, values.whole_file) == (FIRST_ROWS, False)


def test_a_full_runs_values_are_from_every_row(workspace: Workspace) -> None:
    # DRIVN-45 AC-28 with a full run's method (decision A): every row, read again. The reading
    # is the run's saved one, which a full run kept for its masked patterns.
    method, values = profile_jobs.object_values_of(
        workspace,
        FIXTURE,
        postgres_fixture.PASSWORD,
        chosen("big"),
        0,
        "full",
        Reading("page_sample", 10.0),
        7,
    )

    assert method == "full"
    assert (values.rows_read, values.whole_file) == (150_000, True)
    assert (values.minimum, values.maximum) == ("1", "150000")


def test_a_full_runs_values_that_take_longer_than_its_time_limit_are_stopped_and_refused(
    workspace: Workspace,
    drivnx_queries: Callable[[str], int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Decision A: bounded by the run's per-object time limit, and stopped as a profile is. A
    # minute lasts a quarter of a second here, as in the time limit's own test.
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", 0.25)
    started_at = time.monotonic()

    with pytest.raises(profile_jobs.TimeLimitReached) as refused:
        profile_jobs.object_values_of(
            workspace,
            FIXTURE,
            postgres_fixture.PASSWORD,
            chosen("sleepy", "view"),
            0,
            "full",
            Reading("view_first_rows"),
            7,
            time_limit_minutes=1,
        )
    took = time.monotonic() - started_at

    assert refused.value.minutes == 1
    assert took < profile_jobs.SECONDS_PER_MINUTE + STOP_AT_THE_LIMIT_WITHIN_SECONDS, took
    assert drivnx_queries() == 0


def test_a_full_runs_values_may_take_longer_than_other_statements_within_its_time_limit(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As a full run's read (ADR 0009 Decision 3): other statements have 1 s here, and the limit
    # is 10 "minutes" of a second each.
    monkeypatch.setattr(postgres, "STATEMENT_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", 1)

    method, values = profile_jobs.object_values_of(
        workspace,
        FIXTURE,
        postgres_fixture.PASSWORD,
        chosen("three_seconds", "view"),
        0,
        "full",
        Reading("view_first_rows"),
        7,
        time_limit_minutes=10,
    )

    assert (method, values.rows_read) == ("full", 1)


def test_values_while_other_values_are_being_worked_out_are_refused(
    workspace: Workspace, drivnx_queries: Callable[[str], int]
) -> None:
    # As a file's (ADR 0004): two DuckDB jobs could take twice the memory.
    def ask(name: str, mode: str) -> tuple:
        return profile_jobs.object_values_of(
            workspace,
            FIXTURE,
            postgres_fixture.PASSWORD,
            chosen(name, "view" if name == "three_seconds" else "table"),
            0,
            mode,
            Reading("whole"),
            7,
        )

    first: list = []
    asking = threading.Thread(target=lambda: first.append(ask("three_seconds", "full")))
    asking.start()
    try:
        reading(drivnx_queries, f'COPY (SELECT "n" FROM "{SCHEMA}"."three_seconds"%')
        with pytest.raises(profile_jobs.Busy):
            ask("small", "fast")
    finally:
        asking.join(30)

    assert first[0][0] == "full"
    assert ask("small", "fast")[0] == "whole"


def test_values_from_a_database_that_refuses_the_connection_fail_with_its_reason(
    workspace: Workspace,
) -> None:
    # DRIVN-45 AC-28: "Given the database unreachable, then a clear message shows". The
    # connector's reason is written for the user, and the route shows it (`read_failed`).
    def ask(password: str) -> tuple:
        return profile_jobs.object_values_of(
            workspace, FIXTURE, password, chosen("small"), 1, "fast", Reading("whole"), 7
        )

    with pytest.raises(ConnectFailure) as refused:
        ask("not-the-password")  # pragma: allowlist secret, ai-skills: allow

    assert str(refused.value) == "The server refused this user name and password."
    assert ask(postgres_fixture.PASSWORD)[0] == "whole"


def test_a_database_columns_blank_strings_are_values_as_its_run_counted_them(
    workspace: Workspace,
) -> None:
    # DRIVN-45 AC-28, "with that run's method": a run counts only NULL as missing in a database
    # (DRIVN-51), so its values must too (found in review).
    method, values = profile_jobs.object_values_of(
        workspace,
        FIXTURE,
        postgres_fixture.PASSWORD,
        chosen("blanks"),
        0,
        "fast",
        Reading("whole"),
        7,
    )

    assert method == "whole"
    assert values.frequent == (Frequent("", 3), Frequent(" ", 1), Frequent("a", 1))
    assert (values.minimum, values.maximum) == ("", "a")
