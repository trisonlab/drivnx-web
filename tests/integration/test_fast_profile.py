"""Running a fast profile through the API, CFX-APP-csv-profile AC-07, AC-09, AC-13, AC-15, AC-16, AC-17."""

import sqlite3
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import pytest
from artifacts.profiles import read_profile_run
from artifacts.sources import read_dataset
from fastapi.testclient import TestClient
from local_api import profile_jobs
from sqlalchemy.exc import ProgrammingError

WAIT_FOR_A_RUN_SECONDS = 30
CSV_DIR = Path(__file__).parents[2] / "testdata" / "csv"


def connect(client: TestClient, path: Path) -> dict:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 201, response.text
    return response.json()


def start(client: TestClient, source: dict) -> dict:
    response = client.post(
        "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
    )
    assert response.status_code == 202, response.text
    return response.json()


def finished(client: TestClient, run: dict) -> dict:
    """The run once it has ended, as tests mustn't close a workspace with a run still writing."""
    deadline = time.monotonic() + WAIT_FOR_A_RUN_SECONDS
    while time.monotonic() < deadline:
        current = client.get(f"/api/profile-runs/{run['id']}").json()
        if current["status"] != "running":
            return current
        time.sleep(0.01)
    raise AssertionError("The run didn't end.")


def metric_values(run: dict) -> dict[tuple[str | None, str], int]:
    return {
        (metric["column"], metric["metric"]): metric["value"] for metric in run["metrics"]
    }


@pytest.fixture
def held_run(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """Runs wait before reading the file until the event is set, so a test needn't race one."""
    release = threading.Event()
    load_rows = profile_jobs.load_rows

    def held_load_rows(*args, **kwargs):
        assert release.wait(WAIT_FOR_A_RUN_SECONDS)
        return load_rows(*args, **kwargs)

    monkeypatch.setattr(profile_jobs, "load_rows", held_load_rows)
    yield release
    release.set()


def test_a_fast_profile_runs_in_the_background_and_shows_its_results(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-07
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n2,\n3,Cid\n")
    source = connect(client, path)

    run = finished(client, start(client, source))

    assert run["status"] == "completed"
    assert run["source_id"] == source["id"]
    assert run["mode"] == "fast"
    assert metric_values(run) == {
        (None, "rows_read"): 3,
        ("id", "missing_count"): 0,
        ("name", "missing_count"): 1,
    }


def test_a_second_run_is_refused_while_one_is_in_progress(
    client: TestClient, tmp_path: Path, held_run: threading.Event
) -> None:
    # CFX-APP-csv-profile/AC-09
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    other = tmp_path / "other.csv"
    other.write_text("a,b\n1,2\n")
    source = connect(client, path)
    second_source = connect(client, other)
    run = start(client, source)

    refused = [
        client.post("/api/profile-runs", json={"source_id": chosen["id"], "mode": "fast"})
        for chosen in (source, second_source)
    ]

    assert [response.status_code for response in refused] == [409, 409]
    assert "already running" in refused[0].json()["detail"]
    held_run.set()
    assert finished(client, run)["status"] == "completed"


def test_runs_are_listed_newest_first_and_show_one_in_progress(
    client: TestClient, tmp_path: Path, held_run: threading.Event
) -> None:
    # CFX-APP-csv-profile/AC-09: the header and the Profile tab read this.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    held_run.set()
    first = finished(client, start(client, source))
    held_run.clear()
    second = start(client, source)

    listed = client.get("/api/profile-runs").json()

    assert [(run["id"], run["status"]) for run in listed] == [
        (second["id"], "running"),
        (first["id"], "completed"),
    ]
    assert listed[0]["source_id"] == source["id"]
    held_run.set()
    finished(client, second)


def test_a_run_made_before_a_correction_no_longer_uses_the_current_settings(
    client: TestClient, tmp_path: Path
) -> None:
    # Until T11 labels them, the Profile tab shows only runs on the current settings.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n2,Bob\n3,Cid\n4,Dee\n")
    source = connect(client, path)
    run = finished(client, start(client, source))
    assert run["current_settings"] is True

    response = client.put(
        f"/api/sources/{source['id']}/settings",
        json={
            "delimiter": source["delimiter"],
            "encoding": source["encoding"],
            "has_header": False,
            "skip_lines": source["skip_lines"],
        },
    )

    assert response.status_code == 200, response.text
    listed = client.get("/api/profile-runs").json()
    assert [entry["current_settings"] for entry in listed] == [False]


def numbered_csv(path: Path, rows: int) -> Path:
    path.write_text("id\n" + "".join(f"{number}\n" for number in range(rows)))
    return path


@pytest.mark.parametrize(
    ("rows", "scope", "rows_read"),
    [(100_000, "full", 100_000), (100_001, "sample", 100_000)],
)
def test_every_metric_says_what_it_was_measured_on(
    client: TestClient, tmp_path: Path, rows: int, scope: str, rows_read: int
) -> None:
    # CFX-APP-csv-profile/AC-15, AC-16 and AC-17
    source = connect(client, numbered_csv(tmp_path / "numbers.csv", rows))

    run = finished(client, start(client, source))

    assert {(metric["scope"], metric["precision"]) for metric in run["metrics"]} == {
        (scope, "exact")
    }
    assert metric_values(run)[(None, "rows_read")] == rows_read
    assert run["id"]
    assert run["started_at"]
    assert run["ended_at"]


def test_results_stay_hidden_until_the_run_has_completed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-15: a run still in progress may hold only some of its results.
    release = threading.Event()
    saved = threading.Event()
    finish = profile_jobs.finish_profile_run

    def held_finish(*args):
        saved.set()
        assert release.wait(WAIT_FOR_A_RUN_SECONDS)
        finish(*args)

    monkeypatch.setattr(profile_jobs, "finish_profile_run", held_finish)
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    run = start(client, connect(client, path))
    assert saved.wait(WAIT_FOR_A_RUN_SECONDS)

    in_progress = client.get(f"/api/profile-runs/{run['id']}").json()

    release.set()
    assert in_progress["status"] == "running"
    assert in_progress["metrics"] == []
    assert finished(client, run)["metrics"] != []


def test_two_runs_started_at_once_make_one_run(
    client: TestClient, tmp_path: Path, held_run: threading.Event
) -> None:
    # CFX-APP-csv-profile/AC-09
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    both_ready = threading.Barrier(2)

    def start_one(_: int) -> int:
        both_ready.wait()
        return client.post(
            "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
        ).status_code

    with ThreadPoolExecutor(2) as pool:
        statuses = sorted(pool.map(start_one, range(2)))

    assert statuses == [202, 409]
    assert len(client.get("/api/profile-runs").json()) == 1
    held_run.set()
    finished(client, client.get("/api/profile-runs").json()[0])


def test_a_file_whose_columns_changed_fails_rather_than_saving_wrong_evidence(
    client: TestClient, tmp_path: Path, held_run: threading.Event
) -> None:
    # T07 refuses this at the start. A run must still never save a metric against the wrong field.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    run = start(client, connect(client, path))
    path.write_text("name,id\nAnn,1\n")
    held_run.set()

    ended = finished(client, run)

    assert ended["status"] == "failed"
    assert ended["metrics"] == []


def test_a_failed_run_keeps_the_errors_text_to_itself(
    client: TestClient,
    workspace_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    escaped_errors: list[BaseException | None],
) -> None:
    # DuckDB's errors quote rows, and a run must neither save nor log them.
    def failing_measure(*_args, **_kwargs):
        raise duckdb.Error("row 7: Zanzibar")

    monkeypatch.setattr(profile_jobs, "measure_first_rows", failing_measure)
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")

    ended = finished(client, start(client, connect(client, path)))

    wait_for_jobs_to_end()
    assert ended["status"] == "failed"
    # Python would print an error that escapes the thread, with the row in its text.
    assert escaped_errors == []
    captured = capfd.readouterr()
    assert "Zanzibar" not in captured.out + captured.err
    assert b"Zanzibar" not in (workspace_dir / "workspace.db").read_bytes()
    assert b"Zanzibar" not in (workspace_dir / "workspace.db-wal").read_bytes()


@pytest.fixture
def escaped_errors(monkeypatch: pytest.MonkeyPatch) -> list[BaseException | None]:
    """Errors that left a job's thread. Python prints these, and pytest would otherwise take them."""
    escaped: list[BaseException | None] = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: escaped.append(args.exc_value)
    )
    return escaped


def wait_for_jobs_to_end() -> None:
    """A run's status shows before an escaping error is reported."""
    for thread in threading.enumerate():
        if thread.name == profile_jobs.JOB_THREAD_NAME:
            thread.join(WAIT_FOR_A_RUN_SECONDS)


@pytest.mark.parametrize(
    "mistake",
    [
        KeyError("a mistake in Conflux"),
        ValueError("This profile run isn't running."),
        ProgrammingError("SELECT nothing", {}, Exception("no such table")),
    ],
    ids=["KeyError", "ValueError", "SQLAlchemyError"],
)
def test_a_mistake_in_a_job_ends_the_run_and_stays_visible(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    escaped_errors: list[BaseException | None],
    mistake: Exception,
) -> None:
    # Left `running`, it would refuse every later run until Conflux restarts.
    def buggy_measure(*_args, **_kwargs):
        raise mistake

    monkeypatch.setattr(profile_jobs, "measure_first_rows", buggy_measure)
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")

    ended = finished(client, start(client, connect(client, path)))

    wait_for_jobs_to_end()
    assert ended["status"] == "failed"
    assert escaped_errors == [mistake]


def test_a_failed_run_does_not_stop_the_next_one(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-09: only a run in progress refuses another.
    measure = profile_jobs.measure_first_rows

    def failing_measure(*_args, **_kwargs):
        raise duckdb.Error("Can't be read")

    monkeypatch.setattr(profile_jobs, "measure_first_rows", failing_measure)
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    assert finished(client, start(client, source))["status"] == "failed"
    monkeypatch.setattr(profile_jobs, "measure_first_rows", measure)

    assert finished(client, start(client, source))["status"] == "completed"


def test_a_file_too_wide_for_one_transaction_saves_every_metric(
    client: TestClient, tmp_path: Path
) -> None:
    # ADR 0002 point 3: at most 1,000 observations a transaction.
    path = tmp_path / "wide.csv"
    names = ",".join(f"c{number}" for number in range(1_500))
    values = ",".join("1" for _ in range(1_500))
    path.write_text(f"{names}\n{values}\n")

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "completed"
    assert len(run["metrics"]) == 1_501


def test_a_job_spills_only_inside_the_workspace_and_stays_within_its_memory_limit(
    client: TestClient,
    workspace_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CFX-APP-csv-profile/AC-13
    current = tmp_path / "current"
    current.mkdir()
    monkeypatch.chdir(current)
    path = tmp_path / "big_cells.csv"
    cell = "".join(chr(0x41 + number % 26) for number in range(1_000))
    path.write_text(
        "id,text\n" + "".join(f"{row},{row}{cell}\n" for row in range(100_000))
    )
    source = connect(client, path)
    seen: dict[str, object] = {}
    measure = profile_jobs.measure_first_rows

    def watching_measure(connection, *args):
        seen["limit"] = connection.execute(
            "SELECT current_setting('memory_limit')"
        ).fetchone()[0]
        seen["spilled"] = list((workspace_dir / "duckdb-tmp").rglob("*"))
        return measure(connection, *args)

    monkeypatch.setattr(profile_jobs, "measure_first_rows", watching_measure)
    monkeypatch.setattr(profile_jobs, "PROFILE_MEMORY_LIMIT", "100MB")

    run = finished(client, start(client, source))

    assert run["status"] == "completed"
    assert seen["limit"] == "95.3 MiB"
    assert seen["spilled"] != []
    assert list(current.iterdir()) == []
    assert list((workspace_dir / "duckdb-tmp").iterdir()) == []


def test_starting_a_run_needs_a_source_and_a_mode_built_so_far(
    client: TestClient, tmp_path: Path
) -> None:
    # T06 adds `full`.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)

    no_source = client.post(
        "/api/profile-runs", json={"source_id": "nope", "mode": "fast"}
    )
    full = client.post(
        "/api/profile-runs", json={"source_id": source["id"], "mode": "full"}
    )

    assert no_source.status_code == 404
    assert full.status_code == 422
    assert client.get("/api/profile-runs/nope").status_code == 404
    assert client.get("/api/profile-runs").json() == []


def test_a_file_that_has_gone_is_refused_before_a_run_starts(
    client: TestClient, tmp_path: Path
) -> None:
    # T07 proves the rest of AC-11.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    path.unlink()

    response = client.post(
        "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
    )

    assert response.status_code == 409
    assert "can't find this file" in response.json()["detail"]
    assert client.get("/api/profile-runs").json() == []


@pytest.mark.parametrize(
    "name", sorted(path.name for path in CSV_DIR.iterdir() if path.is_file())
)
def test_every_file_that_can_be_connected_can_be_profiled(
    client: TestClient, name: str
) -> None:
    # CFX-APP-csv-profile/AC-07: the names a job reads must match the ones saved when connecting.
    response = client.post("/api/sources", json={"path": str(CSV_DIR / name)})
    if response.status_code != 201:
        pytest.skip("This file can't be connected, by design.")

    run = finished(client, start(client, response.json()))

    assert run["status"] == "completed"


def test_a_busy_workspace_says_so(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # T03's review: this was a bare 500, with "Conflux is busy" never reaching the user.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    writer = sqlite3.connect(workspace_dir / "workspace.db", isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        response = client.post(
            "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
        )
    finally:
        writer.execute("ROLLBACK")
        writer.close()

    assert response.status_code == 503
    assert "busy" in response.json()["detail"]


def test_a_field_past_the_saved_columns_gets_no_metric(client: TestClient) -> None:
    # CFX-APP-csv-profile/AC-07: a row wider than the rest reads as an extra column, which has no
    # saved field to point at. The saved columns are still measured correctly.
    source = connect(client, CSV_DIR / "extra_field_in_a_row.csv")
    assert [column["name"] for column in source["columns"]] == ["id", "name", "amount"]

    run = finished(client, start(client, source))

    assert run["status"] == "completed"
    assert metric_values(run) == {
        (None, "rows_read"): 9,
        ("id", "missing_count"): 0,
        ("name", "missing_count"): 0,
        ("amount", "missing_count"): 0,
    }


def test_a_wider_row_in_a_file_with_no_header_is_measured_by_position(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-07: past 10 columns DuckDB renumbers its generated names, so `column3`
    # is saved and `column03` is read. Those names say nothing, and only the width can be checked.
    path = tmp_path / "no_header.csv"
    row = "".join(",".join(str(row * 10 + col) for col in range(9)) + "\n" for row in range(1, 6))
    path.write_text(f"10,11,12,,14,15,16,17,18\n{row}1,2,3,4,5,6,7,8,9,10,11\n{row}")
    source = connect(client, path)
    assert source["has_header"] is False
    assert source["columns"][3]["name"] == "column3"

    run = finished(client, start(client, source))

    assert run["status"] == "completed"
    values = metric_values(run)
    assert values[(None, "rows_read")] == 12
    assert values[("column3", "missing_count")] == 1
    assert values[("column8", "missing_count")] == 0
    assert len(run["metrics"]) == 10


def test_a_file_with_no_header_that_lost_columns_fails(
    client: TestClient,
    tmp_path: Path,
    held_run: threading.Event,
    escaped_errors: list[BaseException | None],
) -> None:
    # CFX-APP-csv-profile/AC-07: too few to give every saved field its metric.
    path = tmp_path / "no_header.csv"
    path.write_text("1,2,3\n4,5,6\n7,8,9\n")
    source = connect(client, path)
    assert source["has_header"] is False
    run = start(client, source)
    path.write_text("1,2\n4,5\n7,8\n")
    held_run.set()

    assert finished(client, run)["status"] == "failed"
    wait_for_jobs_to_end()
    # Expected, so it's handled here and doesn't look like a mistake in Conflux.
    assert escaped_errors == []


def test_a_run_records_what_it_read_and_how(
    client: TestClient, tmp_path: Path
) -> None:
    # The spec's low-level design. A run can't be corrected later, and T11 compares against this.
    path = tmp_path / "report.csv"
    path.write_text("Quarterly;report\nid;name\n1;Ann\n2;Bob\n3;Cid\n4;Dee\n")
    source = connect(client, path)
    corrected = client.put(
        f"/api/sources/{source['id']}/settings",
        json={"delimiter": ";", "encoding": "Latin-1", "has_header": True, "skip_lines": 1},
    )
    assert corrected.status_code == 200, corrected.text
    run = finished(client, start(client, source))
    engine = client.app.state.workspace.engine

    stored = read_profile_run(engine, run["id"])

    assert stored.mode == "fast"
    assert stored.sample_size == 100_000
    assert stored.strategy == "first_rows"
    assert stored.scope == {
        "dataset_id": read_dataset(engine, source["id"]).id,
        "delimiter": ";",
        "encoding": "Latin-1",
        "has_header": True,
        "skip_lines": 1,
        "size_bytes": 49,
        "modified_at": corrected.json()["modified_at"],
    }
