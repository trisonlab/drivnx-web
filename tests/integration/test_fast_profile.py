"""Running a fast profile through the API, CFX-APP-csv-profile AC-07, AC-09, AC-13, AC-15, AC-16, AC-17."""

import os
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
from artifacts.store import WorkspaceBusy
from fastapi.testclient import TestClient
from local_api import profile_jobs, profile_runs_api
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


def metric_values(run: dict) -> dict[tuple[str | None, str], int | float | list]:
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
    values = metric_values(run)
    assert values[(None, "rows_read")] == 3
    assert values[("id", "missing_count")] == 0
    assert values[("name", "missing_count")] == 1


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


def settings_of(source: dict, **changes: object) -> dict:
    settings = {
        "delimiter": source["delimiter"],
        "encoding": source["encoding"],
        "has_header": source["has_header"],
        "skip_lines": source["skip_lines"],
    }
    return {**settings, **changes}


def test_a_correction_during_a_run_on_the_same_file_is_refused(
    client: TestClient, tmp_path: Path, held_run: threading.Event
) -> None:
    # CFX-APP-csv-profile/AC-06
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n2,Bob\n")
    source = connect(client, path)
    run = start(client, source)

    response = client.put(
        f"/api/sources/{source['id']}/settings", json=settings_of(source, has_header=False)
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "A profile is running on this file. Wait for it to finish, then save your settings."
    )
    assert client.get("/api/sources").json() == [source]
    held_run.set()
    ended = finished(client, run)
    assert (ended["status"], ended["current_settings"]) == ("completed", True)


def test_a_start_that_meets_a_correction_is_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-06, the other way round: the correction lands just after the start has
    # read the dataset, so the run would record a dataset that's no longer current.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    read = profile_runs_api.read_dataset

    def read_then_correct(*args):
        dataset = read(*args)
        response = client.put(
            f"/api/sources/{source['id']}/settings", json=settings_of(source, has_header=False)
        )
        assert response.status_code == 200, response.text
        return dataset

    monkeypatch.setattr(profile_runs_api, "read_dataset", read_then_correct)

    response = client.post(
        "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "This file's settings changed just now. Start the profile again."
    )
    assert client.get("/api/profile-runs").json() == []


def test_a_correction_while_a_start_reads_the_file_is_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-06: found reviewing T07. A correction saved while the start reads the
    # file's columns gave a completed run, shown as current, that counted the header as a row.
    path = tmp_path / "numbers.csv"
    path.write_text("1,2\n3,4\n")
    source = connect(client, path)
    assert source["has_header"] is False
    sniff = profile_runs_api.sniffed_columns

    def sniff_then_correct(*args):
        found = sniff(*args)
        response = client.put(
            f"/api/sources/{source['id']}/settings", json=settings_of(source, has_header=True)
        )
        assert response.status_code == 200, response.text
        return found

    monkeypatch.setattr(profile_runs_api, "sniffed_columns", sniff_then_correct)

    response = client.post(
        "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "This file's settings changed just now. Start the profile again."
    )
    assert client.get("/api/profile-runs").json() == []


def test_a_correction_to_another_file_during_a_run_is_saved(
    client: TestClient, tmp_path: Path, held_run: threading.Event
) -> None:
    # CFX-APP-csv-profile/AC-06 refuses only what the run reads.
    running = tmp_path / "people.csv"
    running.write_text("id,name\n1,Ann\n")
    other = tmp_path / "other.csv"
    other.write_text("a,b\n1,2\n")
    run = start(client, connect(client, running))
    source = connect(client, other)

    response = client.put(
        f"/api/sources/{source['id']}/settings", json=settings_of(source, has_header=False)
    )

    assert response.status_code == 200, response.text
    assert response.json()["has_header"] is False
    held_run.set()
    finished(client, run)


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


COLUMNS_NO_LONGER_MATCH = (
    "This file's columns no longer match the saved ones. "
    "Open this source's settings and save them to read the columns again."
)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("a,b\n1,2\n", "a,b,secret\n1,2,x\n"),
        ("a,b\n1,2\n", "b,a\n2,1\n"),
        ("a,b,c\n1,2,3\n", "a,b\n1,2\n"),
        ("1,2\n3,4\n", "1,2,3\n4,5,6\n"),
        ("1,2,3\n4,5,6\n", "1,2\n4,5\n"),
    ],
    ids=["new column", "renamed", "removed", "no header, wider", "no header, narrower"],
)
@pytest.mark.parametrize("saved_settings", [False, True], ids=["detected", "saved"])
def test_a_file_whose_columns_changed_is_refused_before_a_run_starts(
    client: TestClient, tmp_path: Path, before: str, after: str, saved_settings: bool
) -> None:
    # CFX-APP-csv-profile/AC-12. A new column is told apart from a wider row, which reads the same.
    path = tmp_path / "people.csv"
    path.write_text(before)
    source = connect(client, path)
    if saved_settings:
        source = corrected(client, source)
    path.write_text(after)

    response = client.post(
        "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == COLUMNS_NO_LONGER_MATCH
    assert client.get("/api/profile-runs").json() == []


def test_saving_the_settings_after_a_refusal_reads_the_columns_again(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-12, the second half.
    path = tmp_path / "people.csv"
    path.write_text("a,b\n1,2\n")
    source = connect(client, path)
    path.write_text("a,b,c\n1,2,3\n")
    refused = client.post("/api/profile-runs", json={"source_id": source["id"], "mode": "fast"})
    assert refused.status_code == 409

    saved = corrected(client, source)
    run = finished(client, start(client, saved))

    assert [column["name"] for column in saved["columns"]] == ["a", "b", "c"]
    assert run["status"] == "completed"
    assert metric_values(run)[("c", "missing_count")] == 0


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
    # CFX-APP-csv-profile/AC-11: a reason of Conflux's own, not DuckDB's.
    assert ended["failure"] == "Conflux couldn't profile this file."
    # Python would print an error that escapes the thread, with the row in its text.
    assert escaped_errors == []
    captured = capfd.readouterr()
    assert "Zanzibar" not in captured.out + captured.err
    assert b"Zanzibar" not in (workspace_dir / "workspace.db").read_bytes()
    assert b"Zanzibar" not in (workspace_dir / "workspace.db-wal").read_bytes()


def test_a_run_that_meets_a_busy_workspace_says_so(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-11: the file is fine, so the message mustn't blame it.
    def busy_save(*_args) -> None:
        raise WorkspaceBusy("Conflux is busy writing to this workspace. Try again.")

    monkeypatch.setattr(profile_jobs, "add_observations", busy_save)
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "failed"
    assert run["failure"] == "Conflux is busy writing to this workspace. Try again."


def test_a_run_that_runs_out_of_memory_says_so(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-11: nothing is wrong with the file, so the message mustn't blame a row.
    path = tmp_path / "wide_cells.csv"
    path.write_text("id,name\n" + "".join(f"{row},{'x' * 2000}\n" for row in range(3000)))
    source = connect(client, path)
    monkeypatch.setattr(profile_jobs, "PROFILE_MEMORY_LIMIT", "1MB")

    run = finished(client, start(client, source))

    assert run["status"] == "failed"
    assert run["failure"] == "Conflux ran out of memory profiling this file."


def test_a_row_that_cant_be_read_fails_the_run_and_says_so(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-11, and T04's decision: a wider row past the sample fails the run
    # rather than being cut.
    path = tmp_path / "wide.csv"
    rows = "".join(f"{number},Ann\n" for number in range(30_000))
    path.write_text(f"id,name\n{rows}1,Ann,extra\n")

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "failed"
    assert run["failure"] == (
        "A row in this file can't be read, so it can't be profiled without leaving that row out."
    )


@pytest.mark.parametrize("rows_after", [500, 100_500], ids=["small file", "past the first rows"])
def test_a_quote_never_closed_late_in_a_file_fails_the_run(
    client: TestClient, tmp_path: Path, rows_after: int
) -> None:
    # CFX-APP-csv-profile/AC-11: DuckDB 2.0.0 drops every row from an unclosed quote onwards without
    # an error once it comes after 2,046 rows, so the run would claim a short count as the file.
    path = tmp_path / "late_quote.csv"
    before = "".join(f"{number},v{number}\n" for number in range(3000))
    after = "".join(f"{number},w{number}\n" for number in range(rows_after))
    path.write_text(f'id,name\n{before}2,"oops\n{after}')

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "failed"
    assert run["failure"] == (
        "A row in this file can't be read, so it can't be profiled without leaving that row out."
    )


def test_lines_left_out_above_the_table_are_saved_with_the_run(
    client: TestClient, tmp_path: Path
) -> None:
    # As in the preview (first slice, AC-27): DuckDB takes the lines above a wider last row as lines
    # to skip, so a run must say how many it left out, or 1 row would look like the whole file.
    path = tmp_path / "last_row_wider.csv"
    path.write_text("".join(f"{number},x\n" for number in range(40)) + "1,2,3\n")

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "completed"
    values = metric_values(run)
    assert (values[(None, "rows_read")], values[(None, "skipped_lines")]) == (1, 40)


def test_a_file_with_no_lines_above_the_table_saves_none_left_out(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")

    run = finished(client, start(client, connect(client, path)))

    assert (None, "skipped_lines") not in metric_values(run)


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
    # ADR 0002 point 3: at most 1,000 observations a transaction, and each column has several.
    path = tmp_path / "wide.csv"
    names = ",".join(f"c{number}" for number in range(200))
    values = ",".join("1" for _ in range(200))
    path.write_text(f"{names}\n{values}\n")

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "completed"
    # Every column's 9 metrics, as its one value has lengths, percentages and a pattern.
    assert len(run["metrics"]) == 1 + 200 * 9


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
    # CFX-APP-csv-profile/AC-11
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


def after_reading(monkeypatch: pytest.MonkeyPatch, change) -> None:
    """Do something to the file once the job has read its rows."""
    load_rows = profile_jobs.load_rows

    def load_then_change(*args, **kwargs):
        loaded = load_rows(*args, **kwargs)
        change()
        return loaded

    monkeypatch.setattr(profile_jobs, "load_rows", load_then_change)


def before_reading(monkeypatch: pytest.MonkeyPatch, change) -> None:
    """Do something to the file once the run has started, just before the job reads it."""
    load_rows = profile_jobs.load_rows

    def change_then_load(*args, **kwargs):
        change()
        return load_rows(*args, **kwargs)

    monkeypatch.setattr(profile_jobs, "load_rows", change_then_load)


def test_a_file_removed_during_a_run_fails_and_says_so(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-11
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    after_reading(monkeypatch, path.unlink)

    run = finished(client, start(client, source))

    assert run["status"] == "failed"
    assert run["failure"] == "Conflux can't find this file. It may have moved or been deleted."
    assert run["metrics"] == []


def test_a_file_changed_during_a_run_fails_and_says_so(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-11: what was measured may not be the file any more.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)

    def add_a_row() -> None:
        with path.open("a") as file:
            file.write("2,Bob\n")

    after_reading(monkeypatch, add_a_row)

    run = finished(client, start(client, source))

    assert run["status"] == "failed"
    assert run["failure"] == "The file changed while it was being profiled. Profile it again."


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_a_file_that_becomes_unreadable_during_a_run_fails_and_says_so(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-11
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    before_reading(monkeypatch, lambda: path.chmod(0))

    try:
        run = finished(client, start(client, source))
    finally:
        path.chmod(0o600)

    assert run["status"] == "failed"
    assert run["failure"] == "Conflux doesn't have permission to read this file."


def test_a_file_replaced_by_a_pipe_during_a_run_still_ends_the_run(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-11: opening a pipe waits for a writer, which never comes.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)

    def replace_with_a_pipe() -> None:
        path.unlink()
        os.mkfifo(path)

    after_reading(monkeypatch, replace_with_a_pipe)

    run = finished(client, start(client, source))

    assert run["status"] == "failed"
    assert run["failure"] == "The file changed while it was being profiled. Profile it again."


def test_a_file_that_cant_be_looked_at_during_a_run_still_says_why(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-11: a link to itself is neither missing nor forbidden.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)

    def replace_with_a_loop() -> None:
        path.unlink()
        path.symlink_to(path)

    after_reading(monkeypatch, replace_with_a_loop)

    run = finished(client, start(client, source))

    assert run["status"] == "failed"
    assert run["failure"] == "Conflux couldn't read this file."


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_a_file_that_cant_be_read_is_refused_before_a_run_starts(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-11
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    path.chmod(0)

    try:
        response = client.post(
            "/api/profile-runs", json={"source_id": source["id"], "mode": "fast"}
        )
    finally:
        path.chmod(0o600)

    assert response.status_code == 409
    assert response.json()["detail"] == "Conflux doesn't have permission to read this file."
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


@pytest.mark.parametrize(
    "name", sorted(path.name for path in CSV_DIR.iterdir() if path.is_file())
)
def test_every_file_can_be_profiled_after_saving_its_settings(
    client: TestClient, name: str
) -> None:
    # CFX-APP-csv-profile/AC-12: saved settings read the columns the preview's way, so a wider
    # row's extra column is saved too, and the check at the start mustn't refuse it.
    response = client.post("/api/sources", json={"path": str(CSV_DIR / name)})
    if response.status_code != 201:
        pytest.skip("This file can't be connected, by design.")
    saved = client.put(
        f"/api/sources/{response.json()['id']}/settings", json=settings_of(response.json())
    )
    if saved.status_code != 200:
        pytest.skip("These settings can't be saved for this file, by design.")

    run = finished(client, start(client, saved.json()))

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


def test_a_busy_workspace_says_so_when_saving_settings_too(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # T03's note: saving settings answered a bare 500 when another program held the write lock.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    writer = sqlite3.connect(workspace_dir / "workspace.db", isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        response = client.put(
            f"/api/sources/{source['id']}/settings", json=settings_of(source, has_header=False)
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
    values = metric_values(run)
    assert values[(None, "rows_read")] == 9
    assert {column for column, _ in values} == {None, "id", "name", "amount"}


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
    assert {column for column, _ in values} == {None, *(f"column{n}" for n in range(9))}


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


def test_each_column_gets_its_lengths_distinct_count_and_percentages(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14. Worked by hand: 4 rows, and `city` has 1 missing and 3 values of
    # which 2 differ, with lengths 3, 3 and 5.
    path = tmp_path / "people.csv"
    path.write_text("id,city\n1,Rio\n2,Rio\n3,\n4,Paris\n")

    run = finished(client, start(client, connect(client, path)))

    values = metric_values(run)
    assert values[("city", "missing_count")] == 1
    assert values[("city", "missing_percent")] == 25.0
    assert values[("city", "distinct_count")] == 2
    assert values[("city", "distinct_percent")] == 66.67
    assert values[("city", "shortest_length")] == 3
    assert values[("city", "longest_length")] == 5
    assert values[("city", "average_length")] == 3.67


def test_a_header_with_no_rows_saves_nothing_it_would_have_to_divide_by(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14
    path = tmp_path / "header_only.csv"
    path.write_text("id,name\n")

    run = finished(client, start(client, connect(client, path)))

    assert run["status"] == "completed"
    values = metric_values(run)
    assert values[(None, "rows_read")] == 0
    assert {metric for column, metric in values if column == "name"} == {
        "missing_count",
        "distinct_count",
        "not_fitting_count",
    }


def test_a_completely_empty_column_has_no_lengths_or_distinct_percentage(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14: there are rows, so its missing percentage still means something.
    path = tmp_path / "empty_column.csv"
    path.write_text("id,blank\n1,\n2,   \n3,\n4,\n")

    run = finished(client, start(client, connect(client, path)))

    values = metric_values(run)
    assert values[("blank", "missing_count")] == 4
    assert values[("blank", "missing_percent")] == 100.0
    assert {metric for column, metric in values if column == "blank"} == {
        "missing_count",
        "missing_percent",
        "distinct_count",
        "not_fitting_count",
    }
    assert values[("blank", "distinct_count")] == 0


def test_columns_with_the_same_name_are_measured_separately(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14
    path = tmp_path / "duplicates.csv"
    path.write_text("a,a\nxx,yyyy\nzz,yyyy\nww,yyyy\n")

    source = connect(client, path)
    run = finished(client, start(client, source))

    first, second = [column["name"] for column in source["columns"]]
    values = metric_values(run)
    assert values[(first, "distinct_count")] == 3
    assert values[(first, "longest_length")] == 2
    assert values[(second, "distinct_count")] == 1
    assert values[(second, "longest_length")] == 4


@pytest.mark.parametrize(
    ("good", "late", "not_fitting"),
    [
        ("7", ["1.5", "abc", "12", ""], 2),
        ("1.5", ["2", "abc"], 1),
        ("true", ["false", "maybe"], 1),
        ("2024-03-05", ["2024-02-30", "2024-02-28", "soon"], 2),
        ("03/25/2024", ["12/31/2024", "31/12/2024"], 1),
        ("25/03/2024", ["31/12/2024", "12/31/2024"], 1),
        ("2024-03-05 10:00:00", ["2024-03-05 25:00:00", "2024-03-06 11:00:00"], 1),
        ("25/03/2024 10:00:00", ["31/12/2024 23:59:00", "12/31/2024 10:00:00"], 1),
        ("10:00:00", ["25:00:00", "11:30:00"], 1),
        ("abc", ["1", "\N{GRINNING FACE}"], 0),
    ],
    ids=[
        "integer",
        "number",
        "boolean",
        "date",
        "us_date",
        "eu_date",
        "timestamp",
        "eu_timestamp",
        "time",
        "text",
    ],
)
def test_values_past_the_detection_sample_are_checked_against_the_type(
    client: TestClient, tmp_path: Path, good: str, late: list[str], not_fitting: int
) -> None:
    # CFX-APP-csv-profile/AC-14: DuckDB detects a type from about the first 20,000 rows.
    path = tmp_path / "typed.csv"
    rows = [f"{good},1\n"] * 20_600 + [f"{value},1\n" for value in late]
    path.write_text("value,other\n" + "".join(rows), encoding="utf-8")
    source = connect(client, path)

    run = finished(client, start(client, source))

    assert run["status"] == "completed"
    assert metric_values(run)[("value", "not_fitting_count")] == not_fitting


def test_every_value_in_a_date_format_other_than_iso_fits(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14: a plain cast reports each of these as not fitting.
    path = tmp_path / "dates.csv"
    path.write_text("joined,x\n03/25/2024,1\n03/26/2024,2\n12/27/2024,3\n")
    source = connect(client, path)
    assert source["columns"][0]["physical_type"] == "DATE"

    run = finished(client, start(client, source))

    assert metric_values(run)[("joined", "not_fitting_count")] == 0


@pytest.mark.parametrize(
    ("contents", "physical_type", "not_fitting"),
    [
        pytest.param(
            b"joined,x\r\n03/25/2024,1\n03/26/2024,2\r\n12/27/2024,3\n",
            "DATE",
            0,
            id="mixed line endings",
        ),
        pytest.param(
            b"joined,x\r\n25/03/2024 10:00:00,1\n26/03/2024 11:00:00,2\r\n27/12/2024 12:00:00,3\n",
            "TIMESTAMP",
            0,
            id="timestamps with mixed line endings",
        ),
        pytest.param(
            "joined,x\r\n03/25/2024,1\n03/26/2024,2\r\n12/27/2024,3\n".encode("utf-16"),
            "DATE",
            0,
            id="decoded in memory with mixed line endings",
        ),
        pytest.param(
            b"joined,x\n03/25/2024,1\n03/26/2024,2\n12/27/2024\n",
            "DATE",
            0,
            id="short row",
        ),
        pytest.param(
            b"joined,x\n25/03/2024 10:00:00,1\n26/03/2024 11:00:00,2\n27/12/2024 12:00:00\n",
            "TIMESTAMP",
            0,
            id="timestamps with a short row",
        ),
        # Past the sample, so the column is still detected as a date. It must still count.
        pytest.param(
            b"joined,x\r\n"
            + b"03/25/2024,1\n03/26/2024,2\r\n" * 15_000
            + b"not a date,3\n",
            "DATE",
            1,
            id="a value that really doesn't fit",
        ),
    ],
)
def test_a_format_other_than_iso_fits_however_the_file_had_to_be_read(
    client: TestClient,
    tmp_path: Path,
    contents: bytes,
    physical_type: str,
    not_fitting: int,
) -> None:
    # CFX-APP-csv-profile/AC-14: found verifying T05. Detecting the format strictly failed on these
    # files, which left a plain cast to report every value as not fitting.
    path = tmp_path / "dates.csv"
    path.write_bytes(contents)
    source = connect(client, path)
    assert source["columns"][0]["physical_type"] == physical_type

    run = finished(client, start(client, source))

    assert run["status"] == "completed"
    assert metric_values(run)[("joined", "not_fitting_count")] == not_fitting


@pytest.mark.parametrize(
    ("contents", "column", "physical_type"),
    [
        pytest.param(
            "id,a,b\n1,2024-03-25,2024/03/25\n2,2024-03-26,2024/03/26\n3,2024-03-27,2024/03/27\n",
            "b",
            "DATE",
            id="dates",
        ),
        pytest.param(
            "id,local,utc\n"
            "1,25/03/2024 10:00:00,2024-03-25 09:00:00\n"
            "2,26/03/2024 10:00:00,2024-03-26 09:00:00\n"
            "3,27/03/2024 10:00:00,2024-03-27 09:00:00\n",
            "utc",
            "TIMESTAMP WITH TIME ZONE",
            id="timestamps",
        ),
    ],
)
def test_a_column_in_another_format_of_its_type_fits(
    client: TestClient, tmp_path: Path, contents: str, column: str, physical_type: str
) -> None:
    # CFX-APP-csv-profile/AC-14: found reviewing T05. DuckDB detects one format per type, and still
    # types a second column that its plain cast can read.
    path = tmp_path / "formats.csv"
    path.write_text(contents)
    source = connect(client, path)
    assert source["columns"][2]["physical_type"] == physical_type

    run = finished(client, start(client, source))

    assert metric_values(run)[(column, "not_fitting_count")] == 0


@pytest.mark.parametrize(
    ("values", "physical_type"),
    [
        (["-9345230074912938", "-9253028069769886", "-9066624320668406"], "BIGINT"),
        (["65573643442266533278", "65573643442266533279"], "HUGEINT"),
        (["170141183460469231731687303715884105727", "5"], "HUGEINT"),
    ],
    ids=["large_negative", "above_2_to_the_64", "largest_hugeint"],
)
def test_a_large_integer_fits_an_integer_column(
    client: TestClient, tmp_path: Path, values: list[str], physical_type: str
) -> None:
    # CFX-APP-csv-profile/AC-14: found reviewing T05. Converted two ways, the same whole number can
    # round to two different doubles.
    path = tmp_path / "integers.csv"
    path.write_text("id,big\n" + "".join(f"{row},{value}\n" for row, value in enumerate(values)))
    source = connect(client, path)
    assert source["columns"][1]["physical_type"] == physical_type

    run = finished(client, start(client, source))

    assert metric_values(run)[("big", "not_fitting_count")] == 0


def test_skipping_every_line_leaves_nothing_to_measure_and_the_run_still_finishes(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14: found verifying T05. DuckDB can't sniff a format from no rows, and
    # with nothing to fit it doesn't need one.
    path = tmp_path / "header_only.csv"
    path.write_text("id,name\n")
    source = corrected(client, connect(client, path), skip_lines=1)

    run = finished(client, start(client, source))

    assert run["status"] == "completed"
    assert metric_values(run)[(None, "rows_read")] == 0


def paged_through_the_preview(client: TestClient, source: dict) -> tuple[list, list[list]]:
    """Every column name and row the preview shows, a page at a time."""
    rows: list[list] = []
    offset = 0
    while True:
        page = client.get(f"/api/sources/{source['id']}/rows", params={"offset": offset})
        assert page.status_code == 200, page.text
        body = page.json()
        rows += [[cell["value"] for cell in row] for row in body["rows"]]
        if not body["has_next"]:
            return [cell["value"] for cell in body["columns"]], rows
        offset += len(body["rows"])


def corrected(client: TestClient, source: dict, **changes: object) -> dict:
    settings = {
        "delimiter": source["delimiter"],
        "encoding": source["encoding"],
        "has_header": source["has_header"],
        "skip_lines": source["skip_lines"],
        **changes,
    }
    response = client.put(f"/api/sources/{source['id']}/settings", json=settings)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("contents", "changes"),
    [
        (
            "id,name\n1,Caf\u00e9\n2,\n3,Zo\u00eb\n4,\u20ac5\n".encode("cp1252"),
            {"encoding": "Windows-1252"},
        ),
        (b"id,name\n1,Ann\r\n2,\n3,Cid\r\n4,Dee\n", {}),
        (
            b"id,note\n"
            + b"".join(b"%d,plain\n" % row for row in range(30_000))
            + b'30000,"a, quoted one"\n30001,\n',
            {},
        ),
        ((CSV_DIR / "title_line.csv").read_bytes(), {"skip_lines": 0}),
        ((CSV_DIR / "title_line.csv").read_bytes(), {"skip_lines": 0, "has_header": False}),
    ],
    ids=[
        "decoded_in_memory",
        "mixed_line_endings",
        "quote_first_seen_late",
        "uneven_lines_left_in_the_table",
        "uneven_lines_left_in_the_table_no_header",
    ],
)
def test_a_profile_reads_a_file_the_same_way_as_the_preview(
    client: TestClient, tmp_path: Path, contents: bytes, changes: dict
) -> None:
    # CFX-APP-csv-profile/AC-14: counted from what the preview shows, not from how it was read.
    path = tmp_path / "file.csv"
    path.write_bytes(contents)
    source = connect(client, path)
    if changes:
        source = corrected(client, source, **changes)
    names, rows = paged_through_the_preview(client, source)

    run = finished(client, start(client, source))

    assert run["status"] == "completed"
    values = metric_values(run)
    assert values[(None, "rows_read")] == len(rows)
    for position, column in enumerate(source["columns"]):
        assert names[position] == column["name"]
        cells = [row[position] for row in rows]
        missing = sum(1 for cell in cells if cell is None or not cell.strip())
        assert values[(column["name"], "missing_count")] == missing


def test_a_surplus_column_is_counted_as_not_measured(client: TestClient) -> None:
    # CFX-APP-csv-profile/AC-14, by the user's decision: it has no saved field to give metrics to,
    # and the tab says so from this count.
    source = connect(client, CSV_DIR / "extra_field_in_a_row.csv")

    run = finished(client, start(client, source))

    assert metric_values(run)[(None, "extra_columns")] == 1


def test_a_file_with_no_surplus_columns_says_nothing_about_them(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n2,Bob\n")

    run = finished(client, start(client, connect(client, path)))

    assert (None, "extra_columns") not in metric_values(run)
