"""Profiling a Parquet file, DRIVN-60 AC-12, AC-13, AC-30, AC-31 (DRIVN-63)."""

import threading
import time
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from artifacts.profiles import read_profile_run
from connectors.parquet_files import load_rows, sample_slices
from fastapi.testclient import TestClient
from local_api import profile_jobs

from ..conftest import posix_only
from .test_fast_profile import WAIT_FOR_A_RUN_SECONDS, finished, metric_values, start
from .test_full_profile import CANCEL_WITHIN_SECONDS

ROWS = 400_000
ROW_GROUP_ROWS = 50_000


def written(path: Path, select: str, row_group_rows: int = ROW_GROUP_ROWS) -> Path:
    """A Parquet file DuckDB writes from `select`, in one thread so each row group holds exactly
    `row_group_rows` rows, in order."""
    with duckdb.connect() as connection:
        connection.execute("SET threads = 1")
        connection.execute(
            f"COPY ({select}) TO '{path}' (FORMAT parquet, ROW_GROUP_SIZE {row_group_rows})"
        )
    return path


@pytest.fixture
def events(tmp_path: Path) -> Path:
    # `flag` is missing on some rows, so how many are missing depends on which rows were read.
    return written(
        tmp_path / "events.parquet",
        f"""
        SELECT
            i::BIGINT AS id,
            'event ' || i AS name,
            CASE WHEN hash(i) % 7 = 0 THEN NULL ELSE i % 3 END AS flag
        FROM range({ROWS}) t(i)
        """,
    )


def connect(client: TestClient, path: Path) -> dict:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "parquet"
    return response.json()


def test_a_fast_profile_reads_about_100000_rows_as_a_sample(
    client: TestClient, events: Path
) -> None:
    # DRIVN-60/AC-12
    source = connect(client, events)

    run = finished(client, start(client, source))

    assert run["status"] == "completed", run
    rows_read = next(
        metric
        for metric in run["metrics"]
        if metric["column"] is None and metric["metric"] == "rows_read"
    )
    assert (rows_read["value"], rows_read["scope"]) == (100_000, "sample")
    assert metric_values(run)[("id", "distinct_count")] == 100_000


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Make the next runs' seed `seed`, as a run's seed is otherwise random."""

    def seed_runs(seed: int) -> None:
        monkeypatch.setattr(profile_jobs.secrets, "randbelow", lambda _below: seed)

    return seed_runs


def slices_of(client: TestClient, run: dict) -> list[list[int]]:
    return read_profile_run(client.app.state.workspace.engine, run["id"]).scope["slices"]


def test_the_same_seed_reads_the_same_rows_spread_over_the_file(
    client: TestClient, events: Path, seeded: Callable[[int], None]
) -> None:
    # DRIVN-60/AC-12, sampled as decided with the user on 2026-11-26: 8 slices of 12,500 rows,
    # each starting at a seeded row in its eighth of the file.
    source = connect(client, events)
    seeded(42)
    first = finished(client, start(client, source))
    again = finished(client, start(client, source))
    seeded(43)
    other = finished(client, start(client, source))

    slices = slices_of(client, first)
    assert slices == slices_of(client, again)
    assert slices != slices_of(client, other)
    eighth = ROWS // 8
    for n, (begin, rows) in enumerate(slices):
        assert rows == 12_500
        assert n * eighth <= begin <= (n + 1) * eighth - rows
    # The metrics are of those rows: `flag` is missing on rows that depend on where they are.
    missing = [
        metric_values(run)[("flag", "missing_count")] for run in (first, again, other)
    ]
    assert missing[0] == missing[1] != missing[2]


def test_a_column_named_like_duckdbs_row_number_is_sampled_as_any_other(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-12: DuckDB numbers a file's rows in a column of this name, which the file's own
    # mustn't be mistaken for.
    path = written(
        tmp_path / "numbered.parquet",
        f"SELECT i + 7000000 AS file_row_number, 'x' || i AS v FROM range({ROWS}) t(i)",
    )

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "completed", run
    assert metric_values(run)[("file_row_number", "distinct_count")] == 100_000


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_the_row_count_comes_from_the_files_metadata(
    client: TestClient, events: Path, mode: str
) -> None:
    # DRIVN-60/AC-12: "the row count … is labelled 'from the file's metadata'".
    run = finished(client, start(client, connect(client, events), mode))

    counted = [
        (metric["value"], metric["scope"])
        for metric in run["metrics"]
        if metric["column"] is None and metric["metric"] == "row_count"
    ]
    assert counted == [(ROWS, "metadata")]


def test_a_full_profile_reads_every_row_with_patterns_from_the_seeded_sample(
    client: TestClient, events: Path, seeded: Callable[[int], None]
) -> None:
    # DRIVN-60/AC-12: "A full profile reads all"; its masked patterns come from the rows a fast
    # profile with its seed reads, as a database's do (decided with the user on 2026-11-26).
    seeded(42)
    source = connect(client, events)
    fast = finished(client, start(client, source))

    run = finished(client, start(client, source, "full"))

    assert run["status"] == "completed", run
    scoped = {
        (metric["column"], metric["metric"]): (metric["value"], metric["scope"])
        for metric in run["metrics"]
    }
    assert scoped[(None, "rows_read")] == (ROWS, "full")
    assert scoped[(None, "sample_rows_read")] == (100_000, "sample")
    assert scoped[("id", "distinct_count")] == (ROWS, "full")
    assert scoped[(None, "duplicate_rows")] == (0, "full")
    assert scoped[("name", "masked_patterns")][1] == "sample"
    assert scoped[("name", "masked_patterns")][0] == metric_values(fast)[
        ("name", "masked_patterns")
    ]
    assert slices_of(client, run) == slices_of(client, fast)


def scoped_rows_read(run: dict) -> tuple[int, str]:
    (found,) = [
        (metric["value"], metric["scope"])
        for metric in run["metrics"]
        if metric["column"] is None and metric["metric"] == "rows_read"
    ]
    return found


def test_a_file_no_larger_than_the_sample_is_read_whole(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-30: "Given a Parquet file of 60,000 rows … it reads every row".
    path = written(tmp_path / "small.parquet", "SELECT i AS id FROM range(60000) t(i)")

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "completed", run
    assert scoped_rows_read(run) == (60_000, "full")
    assert slices_of(client, run) is None


def test_one_huge_row_group_is_sampled_inside_it(client: TestClient, tmp_path: Path) -> None:
    # DRIVN-60/AC-30: "a file of 5,000,000 rows in one row group … still reads about 100,000
    # rows, sampled within that row group". The spec's open question, answered by the slices.
    rows = 5_000_000
    path = written(
        tmp_path / "one_group.parquet",
        f"SELECT i AS id FROM range({rows}) t(i)",
        row_group_rows=rows,
    )
    with duckdb.connect() as connection:
        (groups,) = connection.execute(
            f"SELECT count(DISTINCT row_group_id) FROM parquet_metadata('{path}')"
        ).fetchone()
    assert groups == 1, "the file isn't one row group, so this test proves nothing"

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "completed", run
    assert scoped_rows_read(run) == (100_000, "sample")
    assert metric_values(run)[("id", "distinct_count")] == 100_000
    assert len(slices_of(client, run)) == 8


def test_a_file_that_has_gone_is_refused_before_a_run_starts(
    client: TestClient, events: Path
) -> None:
    # DRIVN-60/AC-31, as for a CSV (DRIVN-9/AC-11): a clear message, and no run.
    source = connect(client, events)
    events.unlink()

    response = client.post(
        "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "DrivnX can't find this file. It may have moved or been deleted."
    )
    assert client.get("/api/profile-runs").json() == []


def test_a_file_replaced_by_text_is_refused_before_a_run_starts(
    client: TestClient, events: Path
) -> None:
    # DRIVN-60/AC-31: the message names no path.
    source = connect(client, events)
    events.write_text("id,name\n1,Ann\n", encoding="utf-8")

    response = client.post(
        "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "This file isn't a Parquet file, so it can't be read as one."
    )
    assert client.get("/api/profile-runs").json() == []


def before_reading(monkeypatch: pytest.MonkeyPatch, change: Callable[[], object]) -> None:
    """Do something to the file once the run has started, just before the job reads it."""
    load_rows = profile_jobs.load_parquet_rows

    def change_then_load(*args, **kwargs):
        change()
        return load_rows(*args, **kwargs)

    monkeypatch.setattr(profile_jobs, "load_parquet_rows", change_then_load)


def test_a_file_removed_during_a_run_fails_and_says_so(
    client: TestClient, events: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DRIVN-60/AC-31: "the run ends `failed` …, and its results don't appear as current".
    source = connect(client, events)
    before_reading(monkeypatch, events.unlink)

    run = finished(client, start(client, source))

    assert run["status"] == "failed"
    assert run["failure"] == "DrivnX can't find this file. It may have moved or been deleted."
    assert run["metrics"] == []


@posix_only
def test_a_file_that_cant_be_read_during_a_run_fails_and_says_so(
    client: TestClient, events: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DRIVN-60/AC-31, with the CSV profile's message for a file DrivnX may not read.
    source = connect(client, events)
    before_reading(monkeypatch, lambda: events.chmod(0))

    try:
        run = finished(client, start(client, source))
    finally:
        events.chmod(0o600)

    assert run["status"] == "failed"
    assert run["failure"] == "DrivnX doesn't have permission to read this file."
    assert run["metrics"] == []


def test_cancelling_a_full_profile_stops_its_read_and_it_ends_cancelled(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DRIVN-60/AC-13: "it stops within 5 s, its status is `cancelled`, and none of its results or
    # findings appear as current". Cancelled once the read of every row has started.
    path = written(
        tmp_path / "large.parquet",
        "SELECT i AS id, 'name' || i AS name FROM range(5000000) t(i)",
        row_group_rows=122_880,
    )
    source = connect(client, path)
    reading_every_row = threading.Event()
    raised: list[type[BaseException]] = []
    load_rows = profile_jobs.load_parquet_rows

    def watched(connection, path, table, slices=None):
        if slices is not None:
            return load_rows(connection, path, table, slices)
        reading_every_row.set()
        try:
            return load_rows(connection, path, table, slices)
        except BaseException as error:
            raised.append(type(error))
            raise

    monkeypatch.setattr(profile_jobs, "load_parquet_rows", watched)
    run = start(client, source, "full")
    assert reading_every_row.wait(WAIT_FOR_A_RUN_SECONDS)

    cancelled_at = time.monotonic()
    response = client.post(f"/api/profile-runs/{run['id']}/cancel")
    ended = finished(client, run)

    assert response.status_code == 202, response.text
    assert time.monotonic() - cancelled_at < CANCEL_WITHIN_SECONDS
    assert (ended["status"], ended["metrics"], ended["failure"]) == ("cancelled", [], None)
    assert raised == [duckdb.InterruptException]


@pytest.mark.parametrize(
    "replacement",
    [
        "SELECT i AS a, i AS b, i AS c FROM range(1000) t(i)",
        # The same names, but `id` is now text, which would be measured as a number.
        "SELECT 'x' || i AS id, 'n' || i AS name FROM range(1000) t(i)",
    ],
    ids=["other-columns", "other-type"],
)
def test_a_file_whose_columns_changed_fails_rather_than_saving_wrong_evidence(
    client: TestClient, tmp_path: Path, replacement: str
) -> None:
    # DRIVN-60/AC-31, as a CSV's run does: never a metric saved against the wrong field.
    path = written(
        tmp_path / "events.parquet", "SELECT i AS id, 'n' || i AS name FROM range(1000) t(i)"
    )
    source = connect(client, path)
    written(path, replacement)

    run = finished(client, start(client, source))

    assert run["status"] == "failed"
    assert run["failure"] == "The file's columns no longer match the saved ones."
    assert run["metrics"] == []


def test_a_parquet_runs_values_are_refused_until_they_can_be_worked_out(
    client: TestClient, events: Path
) -> None:
    # Values on request for a Parquet file are DRIVN-64's; until then the route says so, rather
    # than reading the file as a CSV (found in DRIVN-63's review).
    run = finished(client, start(client, connect(client, events)))

    response = client.get(f"/api/profile-runs/{run['id']}/values", params={"column": "name"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "This Parquet file's values can't be worked out yet."


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_a_damaged_value_fails_the_run_as_a_file_that_cant_be_read(
    client: TestClient, tmp_path: Path, mode: str
) -> None:
    # DRIVN-60/AC-31: "DrivnX couldn't read this file." (decided with the user on 2026-11-26).
    # The footer is intact, so the run starts; a value in the rows it reads isn't valid text.
    path = tmp_path / "events.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            "COPY (SELECT i AS id, 'value-' || lpad(i::VARCHAR, 4, '0') AS v FROM range(1000) t(i))"
            f" TO '{path}' (FORMAT parquet, COMPRESSION uncompressed)"
        )
    source = connect(client, path)
    damaged = bytearray(path.read_bytes())
    at = damaged.find(b"value-0500")
    assert at > 0, "the value isn't stored as plain text, so this test proves nothing"
    damaged[at] = 0xFF
    path.write_bytes(bytes(damaged))

    run = finished(client, start(client, source, mode))

    assert run["status"] == "failed"
    assert run["failure"] == "DrivnX couldn't read this file."
    assert run["metrics"] == []


def test_a_sample_loads_exactly_the_rows_of_its_slices(events: Path) -> None:
    # DRIVN-60/AC-12: measuring keeps at most 100,000 rows, so a slice read one row too long
    # would otherwise go unseen (found in DRIVN-63's review). Across row groups and the file's end.
    with duckdb.connect() as connection:
        load_rows(connection, events, "loaded", [(0, 3), (49_999, 2), (399_998, 2)])
        loaded = [
            int(row_id)
            for (row_id,) in connection.execute("SELECT c0 FROM loaded").fetchall()
        ]

    assert sorted(loaded) == [0, 1, 2, 49_999, 50_000, 399_998, 399_999]


@pytest.mark.parametrize("seed", [0, 42, 2**31 - 1])
def test_a_file_of_100000_rows_is_read_whole_and_one_more_is_sampled(seed: int) -> None:
    # DRIVN-60/AC-12 and AC-30, at the boundary (found in DRIVN-63's review).
    assert sample_slices(100_000, seed) is None
    slices = sample_slices(100_001, seed)
    assert len(slices) == 8
    for n, (begin, rows) in enumerate(slices):
        assert rows == 12_500
        assert n * 100_001 // 8 <= begin
        assert begin + rows <= (n + 1) * 100_001 // 8
