"""A full profile through the API, CFX-APP-csv-profile AC-08, AC-10 and AC-18."""

import signal
import threading
import time
from pathlib import Path

import duckdb
import pytest
from artifacts.profiles import (
    mark_interrupted_runs,
    read_findings,
    read_observations,
    read_profile_run,
)
from artifacts.sources import read_dataset
from artifacts.store import WorkspaceBusy
from artifacts.workspace import open_workspace
from fastapi.testclient import TestClient
from local_api import profile_jobs
from local_api.app import create_app
from local_api.session import LaunchSession
from sqlalchemy import text

from ..conftest import CLIENT_ORIGIN, CLIENT_PORT, StartConflux, posix_only
from .test_fast_profile import (
    WAIT_FOR_A_RUN_SECONDS,
    connect,
    finished,
    metric_values,
    start,
    wait_for_jobs_to_end,
)
from .test_session import sign_in

# The spec's target under Non-functional.
CANCEL_WITHIN_SECONDS = 5
SHUTDOWN_WAIT_SECONDS = profile_jobs.SHUTDOWN_WAIT_SECONDS


def planted_missing(path: Path) -> Path:
    """150,001 rows, with `note` blank on rows 10 to 12 and 120,000 to 120,006."""
    blank = {*range(10, 13), *range(120_000, 120_007)}
    path.write_text(
        "id,note\n"
        + "".join(f"{row},{'' if row in blank else 'x'}\n" for row in range(150_001))
    )
    return path


def test_a_full_profile_reads_every_row_and_counts_exactly(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-08
    source = connect(client, planted_missing(tmp_path / "notes.csv"))

    run = finished(client, start(client, source, "full"))

    assert (run["status"], run["mode"]) == ("completed", "full")
    values = metric_values(run)
    assert values[(None, "rows_read")] == 150_001
    assert values[("note", "missing_count")] == 10
    assert {metric["scope"] for metric in run["metrics"] if metric["metric"] != "masked_patterns"} == {"full"}


def test_a_full_run_is_recorded_as_reading_every_row(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-08: a full run has no sample, so no sample size or strategy.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    run = finished(client, start(client, connect(client, path), "full"))

    stored = read_profile_run(client.app.state.workspace.engine, run["id"])

    assert (stored.mode, stored.sample_size, stored.strategy) == ("full", None, None)


def test_a_quote_never_closed_after_the_first_rows_fails_a_full_run(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-08: found planning T07. DuckDB drops every row after it with no error,
    # which a fast run never reaches but a full one would report as the whole file.
    path = tmp_path / "late_quote.csv"
    path.write_text(
        "id,name\n"
        + "".join(f"{row},n{row}\n" for row in range(120_000))
        + '2,"oops\n'
        + "".join(f"{row},n{row}\n" for row in range(500))
    )
    source = connect(client, path)

    run = finished(client, start(client, source, "full"))

    assert run["status"] == "failed"
    assert run["failure"] == (
        "A row in this file can't be read, so it can't be profiled without leaving that row out."
    )


def scopes(run: dict) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for metric in run["metrics"]:
        found.setdefault(metric["metric"], set()).add(metric["scope"])
    return found


def test_a_full_profile_takes_patterns_from_the_first_rows_only(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-18
    source = connect(client, planted_missing(tmp_path / "notes.csv"))

    run = finished(client, start(client, source, "full"))

    found = scopes(run)
    assert found.pop("masked_patterns") == {"sample"}
    assert set().union(*found.values()) == {"full"}
    # `x` is in 99,997 of the first 100,000 rows, and 149,991 of every row.
    assert metric_values(run)[("note", "masked_patterns")] == [
        {"pattern": "A", "count": 99_997, "cut": False}
    ]


def large_csv(path: Path, rows: int = 5_000_000) -> Path:
    """Made by DuckDB, as writing it from Python would take most of the test."""
    with duckdb.connect() as connection:
        connection.execute(
            f"COPY (SELECT range AS id, 'name' || range AS name FROM range({rows:d}))"
            f" TO '{path}' (HEADER)"
        )
    return path


def cancel(client: TestClient, run: dict):
    return client.post(f"/api/profile-runs/{run['id']}/cancel")


def test_cancelling_a_run_stops_its_read_and_it_ends_cancelled(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-10
    source = connect(client, large_csv(tmp_path / "large.csv"))
    reading = threading.Event()
    raised: list[type[BaseException]] = []
    load_rows = profile_jobs.load_rows

    def watched_load_rows(*args, **kwargs):
        reading.set()
        try:
            return load_rows(*args, **kwargs)
        except BaseException as error:
            raised.append(type(error))
            raise

    monkeypatch.setattr(profile_jobs, "load_rows", watched_load_rows)
    run = start(client, source, "full")
    assert reading.wait(WAIT_FOR_A_RUN_SECONDS)

    cancelled_at = time.monotonic()
    response = cancel(client, run)
    ended = finished(client, run)

    assert response.status_code == 202, response.text
    assert time.monotonic() - cancelled_at < CANCEL_WITHIN_SECONDS
    assert (ended["status"], ended["metrics"], ended["failure"]) == ("cancelled", [], None)
    assert raised == [duckdb.InterruptException]
    monkeypatch.setattr(profile_jobs, "load_rows", load_rows)
    assert finished(client, start(client, source))["status"] == "completed"


def test_a_run_that_has_ended_or_doesnt_exist_cant_be_cancelled(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-10
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    run = finished(client, start(client, connect(client, path), "full"))

    ended = cancel(client, run)
    unknown = cancel(client, {"id": "nope"})

    assert (ended.status_code, ended.json()["detail"]) == (
        409,
        "This profile has already finished.",
    )
    assert unknown.status_code == 404
    assert client.get(f"/api/profile-runs/{run['id']}").json()["status"] == "completed"


def test_a_cancel_between_two_queries_still_stops_the_run(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-10: an interrupt while no query runs is lost, so this one lands after
    # the read and before measuring starts.
    source = connect(client, large_csv(tmp_path / "large.csv"))
    between, cancelled = threading.Event(), threading.Event()
    raised: list[type[BaseException]] = []
    measure_rows = profile_jobs.measure_rows

    def measure_after_cancel(*args, **kwargs):
        between.set()
        assert cancelled.wait(WAIT_FOR_A_RUN_SECONDS)
        try:
            return measure_rows(*args, **kwargs)
        except BaseException as error:
            raised.append(type(error))
            raise

    monkeypatch.setattr(profile_jobs, "measure_rows", measure_after_cancel)
    run = start(client, source, "full")
    assert between.wait(WAIT_FOR_A_RUN_SECONDS)

    response = cancel(client, run)
    cancelled.set()
    ended = finished(client, run)

    assert response.status_code == 202, response.text
    assert ended["status"] == "cancelled"
    assert raised == [duckdb.InterruptException]


def test_a_cancel_arriving_as_a_run_finishes_is_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-10: from planning. A run's status changes once, so a cancel that's
    # accepted must be what the run ends as. This one arrives once the run has chosen `completed`,
    # and waits for it to be saved.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    finishing = threading.Event()
    finish_profile_run = profile_jobs.finish_profile_run

    def slow_finish(*args, **kwargs):
        finishing.set()
        time.sleep(0.3)
        return finish_profile_run(*args, **kwargs)

    monkeypatch.setattr(profile_jobs, "finish_profile_run", slow_finish)
    run = start(client, source, "full")
    assert finishing.wait(WAIT_FOR_A_RUN_SECONDS)

    response = cancel(client, run)
    ended = finished(client, run)

    assert (response.status_code, ended["status"]) == (409, "completed")


def test_a_cancel_while_saving_saves_no_more(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-10: a wide file saves in several transactions, which could take longer
    # than the target.
    path = tmp_path / "wide.csv"
    columns = 300
    path.write_text(
        ",".join(f"c{number}" for number in range(columns)) + "\n" + ",".join("x" * columns) + "\n"
    )
    source = connect(client, path)
    saving, cancelled = threading.Event(), threading.Event()
    saved: list[int] = []
    add_observations = profile_jobs.add_observations

    def add_after_cancel(engine, actor, run_id, batch, *rest):
        saved.append(len(batch))
        add_observations(engine, actor, run_id, batch, *rest)
        saving.set()
        assert cancelled.wait(WAIT_FOR_A_RUN_SECONDS)

    monkeypatch.setattr(profile_jobs, "add_observations", add_after_cancel)
    run = start(client, source, "full")
    assert saving.wait(WAIT_FOR_A_RUN_SECONDS)

    response = cancel(client, run)
    cancelled.set()
    ended = finished(client, run)

    assert response.status_code == 202, response.text
    assert (ended["status"], len(saved)) == ("cancelled", 1)


def test_shutting_down_stops_a_run_and_leaves_it_to_be_marked_interrupted(
    tmp_path: Path, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # From T04: a job still holding DuckDB at exit made its binding print warnings. A run that stops
    # because Conflux did isn't one the user cancelled, so the next start marks it `interrupted`.
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<html></html>")
    workspace = open_workspace(workspace_dir)
    session = LaunchSession(CLIENT_PORT)
    reading = threading.Event()
    raised: list[type[BaseException]] = []
    load_rows = profile_jobs.load_rows

    def watched_load_rows(*args, **kwargs):
        reading.set()
        try:
            return load_rows(*args, **kwargs)
        except BaseException as error:
            raised.append(type(error))
            raise

    monkeypatch.setattr(profile_jobs, "load_rows", watched_load_rows)
    app = create_app(session, CLIENT_PORT, workspace, ui_dir)
    with TestClient(app, base_url=CLIENT_ORIGIN, headers={"Origin": CLIENT_ORIGIN}) as client:
        client.post("/api/session", json={"code": session.launch_code})
        source = connect(client, large_csv(tmp_path / "large.csv"))
        run = start(client, source, "full")
        assert reading.wait(WAIT_FOR_A_RUN_SECONDS)
    still_running = [
        thread for thread in threading.enumerate() if thread.name == profile_jobs.JOB_THREAD_NAME
    ]
    wait_for_jobs_to_end()
    left_as = read_profile_run(workspace.engine, run["id"]).status
    workspace.close()
    reopened = open_workspace(workspace_dir)
    mark_interrupted_runs(reopened.engine)
    marked_as = read_profile_run(reopened.engine, run["id"]).status
    with reopened.engine.connect() as connection:
        findings = connection.execute(text("SELECT count(*) FROM finding")).scalar()
    reopened.close()

    assert (still_running, raised) == ([], [duckdb.InterruptException])
    assert (left_as, marked_as) == ("running", "interrupted")
    # CFX-APP-csv-profile/AC-26
    assert findings == 0


@posix_only
def test_stopping_conflux_during_a_run_exits_cleanly(
    start_conflux: StartConflux, tmp_path: Path
) -> None:
    # From T04: Ctrl+C during a run exited, but DuckDB's binding printed "leaked" warnings, as the
    # job still held its connection.
    process = start_conflux("--no-browser")
    client = sign_in(process.wait_for_launch())
    path = large_csv(tmp_path / "large.csv", rows=20_000_000)
    source = client.post("/api/sources", json={"path": str(path)}).json()
    started = client.post("/api/profile-runs", json={"source_id": source["id"], "mode": "full"})
    assert started.status_code == 202, started.text

    process.process.send_signal(signal.SIGINT)

    assert process.process.wait(timeout=SHUTDOWN_WAIT_SECONDS + 5) == 0
    printed = process.all_output()
    assert "leaked" not in printed
    assert "Traceback" not in printed


def test_a_cancelled_run_that_cant_record_its_end_still_stops_interrupting(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    escaped_errors: list[BaseException | None],
) -> None:
    # Found reviewing T06: when another program held the workspace past the busy timeout, the
    # cancel's interrupting thread never ended.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    measuring, cancelled = threading.Event(), threading.Event()
    measure_rows = profile_jobs.measure_rows

    def measure_after_cancel(*args, **kwargs):
        measuring.set()
        assert cancelled.wait(WAIT_FOR_A_RUN_SECONDS)
        return measure_rows(*args, **kwargs)

    def busy_finish(*_args, **_kwargs):
        raise WorkspaceBusy("Conflux is busy writing to this workspace. Try again.")

    monkeypatch.setattr(profile_jobs, "measure_rows", measure_after_cancel)
    monkeypatch.setattr(profile_jobs, "finish_profile_run", busy_finish)
    run = start(client, source, "full")
    assert measuring.wait(WAIT_FOR_A_RUN_SECONDS)
    assert cancel(client, run).status_code == 202
    cancelled.set()
    wait_for_jobs_to_end()

    for thread in threading.enumerate():
        if thread.name == profile_jobs.INTERRUPTING_THREAD_NAME:
            thread.join(1)

    assert [type(error) for error in escaped_errors] == [WorkspaceBusy]
    assert not any(
        thread.name == profile_jobs.INTERRUPTING_THREAD_NAME for thread in threading.enumerate()
    )


def test_a_cancel_as_a_run_starts_waits_for_its_job(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    escaped_errors: list[BaseException | None],
) -> None:
    # CFX-APP-csv-profile/AC-10: found reviewing T06. The run is visible once it's saved, so a
    # cancel can come before its job is known.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    answers: list[int] = []
    start_only_profile_run = profile_jobs.start_only_profile_run

    def saved_then_cancelled(*args, **kwargs):
        run_id = start_only_profile_run(*args, **kwargs)
        canceller = threading.Thread(
            target=lambda: answers.append(cancel(client, {"id": run_id}).status_code)
        )
        canceller.start()
        canceller.join(0.3)
        return run_id

    monkeypatch.setattr(profile_jobs, "start_only_profile_run", saved_then_cancelled)
    run = start(client, source, "full")
    ended = finished(client, run)

    assert (answers, ended["status"]) == ([202], "cancelled")
    # Found building T08: findings were looked for in a run that saved nothing.
    wait_for_jobs_to_end()
    assert escaped_errors == []


def with_copies(path: Path) -> Path:
    """150,000 different rows, then 3 exact copies of row 7."""
    path.write_text(
        "id,name\n"
        + "".join(f"{row},n{row}\n" for row in range(150_000))
        + "7,n7\n" * 3
    )
    return path


@pytest.mark.parametrize(("mode", "counted"), [("full", 3), ("fast", None)])
def test_a_full_profile_counts_rows_that_copy_another(
    client: TestClient, tmp_path: Path, mode: str, counted: int | None
) -> None:
    # CFX-APP-csv-profile/AC-22: three copies of one row are 3 duplicates. Only a full profile
    # reads every row, so a fast one counts none.
    source = connect(client, with_copies(tmp_path / "copies.csv"))

    run = finished(client, start(client, source, mode))

    assert metric_values(run).get((None, "duplicate_rows")) == counted


def meeting_every_kind(path: Path) -> Path:
    """`amount` has a value past the sniffed sample that isn't a whole number, the last row is there
    3 times, a quarter of `note` is missing, `id` is distinct and `country` has one value."""
    rows = [
        f"{row},{'' if row % 4 == 0 else f'n{row % 7}'},FR,{'abc' if row == 29_000 else row % 100}"
        for row in range(30_000)
    ]
    path.write_text("id,note,country,amount\n" + "\n".join([*rows, rows[-1], rows[-1]]) + "\n")
    return path


def found_in(client: TestClient, run: dict) -> list[tuple[str, str | None]]:
    """Each saved finding's kind, and the column its first observation is about."""
    engine = client.app.state.workspace.engine
    cited = {seen.id: seen for seen in read_observations(engine, run["id"])}
    names = {field.id: field.name for field in read_dataset(engine, run["source_id"]).fields}
    return [
        (finding.kind, names.get(cited[finding.observation_ids[0]].field_id))
        for finding in read_findings(engine)
        if finding.profile_run_id == run["id"]
    ]


def test_a_completed_run_saves_a_finding_for_each_column_or_file_concerned(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-21 and CFX-APP-csv-profile/AC-22
    source = connect(client, meeting_every_kind(tmp_path / "every_kind.csv"))

    run = finished(client, start(client, source, "full"))

    assert run["status"] == "completed"
    assert found_in(client, run) == [
        ("not_fitting", "amount"),
        ("duplicate_rows", None),
        ("missing_values", "note"),
        ("possible_identifier", "id"),
        ("single_value", "country"),
    ]


def finding_rows(client: TestClient, run: dict) -> int:
    with client.app.state.workspace.engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM finding WHERE profile_run_id = :run"), {"run": run["id"]}
        ).scalar()


@pytest.mark.parametrize("ending", ["failed", "cancelled"])
def test_a_run_that_doesnt_complete_saves_no_findings_and_earlier_ones_stay(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    # CFX-APP-csv-profile/AC-26
    source = connect(client, meeting_every_kind(tmp_path / "every_kind.csv"))
    earlier = finished(client, start(client, source, "full"))
    before = found_in(client, earlier)
    saving, cancelled = threading.Event(), threading.Event()
    add_observations = profile_jobs.add_observations

    def add_then_end(engine, actor, run_id, batch, *rest):
        add_observations(engine, actor, run_id, batch, *rest)
        if ending == "failed":
            raise duckdb.Error("row 7: Zanzibar")
        saving.set()
        assert cancelled.wait(WAIT_FOR_A_RUN_SECONDS)

    monkeypatch.setattr(profile_jobs, "add_observations", add_then_end)
    run = start(client, source, "full")
    if ending == "cancelled":
        assert saving.wait(WAIT_FOR_A_RUN_SECONDS)
        assert cancel(client, run).status_code == 202
        cancelled.set()
    ended = finished(client, run)

    assert (ended["status"], finding_rows(client, run)) == (ending, 0)
    assert len(before) == 5
    assert found_in(client, earlier) == before


def test_a_cancel_after_findings_are_worked_out_still_saves_none(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-26: the run has its findings ready, but ends as `cancelled`.
    source = connect(client, meeting_every_kind(tmp_path / "every_kind.csv"))
    ready, cancelled = threading.Event(), threading.Event()
    findings_for = profile_jobs.findings_for

    def findings_then_cancel(observations):
        found = findings_for(observations)
        ready.set()
        assert cancelled.wait(WAIT_FOR_A_RUN_SECONDS)
        return found

    monkeypatch.setattr(profile_jobs, "findings_for", findings_then_cancel)
    run = start(client, source, "full")
    assert ready.wait(WAIT_FOR_A_RUN_SECONDS)
    assert cancel(client, run).status_code == 202
    cancelled.set()
    ended = finished(client, run)

    assert (ended["status"], finding_rows(client, run)) == ("cancelled", 0)


def test_saved_findings_hold_no_column_name_or_value_and_cite_evidence(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-25: the names and values are planted to be found if they leak.
    path = tmp_path / "planted.csv"
    rows = [f"Zebulon{row},{'' if row % 3 == 0 else 'Quokkaland'},{row % 9}" for row in range(300)]
    path.write_text("Xanthippe_ref,Marmaduke_region,Ptolemy_count\n" + "\n".join(rows) + "\n")
    planted = ("Xanthippe", "Marmaduke", "Ptolemy", "Zebulon", "Quokkaland")
    run = finished(client, start(client, connect(client, path), "full"))

    with client.app.state.workspace.engine.connect() as connection:
        saved = connection.execute(
            text(
                """
                SELECT finding.kind, finding.interpretation, finding.why_it_matters,
                       finding.recommended_action, count(finding_observation.observation_id)
                FROM finding LEFT JOIN finding_observation ON finding_id = finding.id
                WHERE profile_run_id = :run GROUP BY finding.id
                """
            ),
            {"run": run["id"]},
        ).all()

    assert {row.kind for row in saved} == {"possible_identifier", "missing_values", "single_value"}
    for row in saved:
        text_saved = " ".join(row[:4])
        assert not [word for word in planted if word.lower() in text_saved.lower()]
        assert row[4] >= 1
