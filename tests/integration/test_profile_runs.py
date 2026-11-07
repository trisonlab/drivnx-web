"""How a profile run ends, CFX-APP-first-slice AC-42 and AC-43 (ADR 0002 point 3)."""

import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from artifacts.profiles import (
    NewFinding,
    NewObservation,
    ProfileRun,
    SettingsChanged,
    add_finding,
    add_observations,
    finish_profile_run,
    mark_interrupted_runs,
    read_finding,
    read_findings,
    read_observations,
    read_profile_run,
    start_only_profile_run,
    start_profile_run,
)
from artifacts.sources import (
    StoredField,
    StoredSource,
    add_csv_source,
    find_source,
    read_dataset,
    save_corrected_settings,
)
from artifacts.workspace import Workspace, open_workspace
from domain.trust import Actor
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..conftest import StartConflux

RUN_THEN_WAIT_TO_BE_KILLED = """
import sys, time
from pathlib import Path
from artifacts.profiles import start_profile_run
from artifacts.workspace import open_workspace

workspace = open_workspace(Path(sys.argv[1]))
print(start_profile_run(workspace.engine, scope={}, mode="full"), flush=True)
time.sleep(60)
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Workspace]:
    opened = open_workspace(tmp_path / "workspace")
    yield opened
    opened.close()


@pytest.fixture
def dataset_id(workspace: Workspace) -> str:
    add_csv_source(
        workspace.engine,
        StoredSource(
            id="source-1",
            path=Path("/data/customers.csv"),
            delimiter=",",
            encoding="UTF-8",
            has_header=True,
            size_bytes=10,
            modified_at="2024-01-01T00:00:00+00:00",
            fields=(StoredField("id", "BIGINT", "integer"),),
        ),
    )
    return read_dataset(workspace.engine, "source-1").id


def one_observation(dataset_id: str) -> list[NewObservation]:
    return [NewObservation(dataset_id, None, "row_count", 12, "full", "count", "exact")]


def run_with_finding(workspace: Workspace, dataset_id: str) -> tuple[str, str]:
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id))
    observation = read_observations(workspace.engine, run)[0].id
    finding = add_finding(
        workspace.engine,
        Actor.SYSTEM,
        NewFinding(run, (observation,), "Few rows", "Too few", "Check the export", kind="missing_values"),
    )
    return run, finding


def stored_run(directory: Path, run: str) -> ProfileRun:
    workspace = open_workspace(directory)
    try:
        return read_profile_run(workspace.engine, run)
    finally:
        workspace.close()


def test_a_run_left_running_is_marked_interrupted_at_the_next_start(
    start_conflux: StartConflux, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-43, through the real command: this is what "the next runtime" means.
    directory = tmp_path / "workspace"
    workspace = open_workspace(directory)
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    workspace.close()

    conflux = start_conflux("--no-browser", workspace=directory)
    conflux.wait_for_url()
    conflux.process.terminate()
    conflux.process.wait(timeout=10)

    stored = stored_run(directory, run)
    assert stored.status == "interrupted"
    assert stored.ended_at is not None


def test_a_run_in_a_killed_process_is_marked_interrupted_too(
    start_conflux: StartConflux, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-43: a crash runs no cleanup, and the OS releases its lock (ADR 0001).
    directory = tmp_path / "workspace"
    writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            RUN_THEN_WAIT_TO_BE_KILLED,
            str(directory),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert writer.stdout
    run = writer.stdout.readline().strip()
    assert run, "the process stopped before starting a run"
    writer.kill()
    writer.wait(timeout=10)

    conflux = start_conflux("--no-browser", workspace=directory)
    conflux.wait_for_url()
    conflux.process.terminate()
    conflux.process.wait(timeout=10)

    assert stored_run(directory, run).status == "interrupted"


def test_a_writer_outside_conflux_gives_a_clear_message_at_startup(
    start_conflux: StartConflux, tmp_path: Path
) -> None:
    # Marking runs writes to the database, so startup can now run out of time. Takes the full 5 s.
    directory = tmp_path / "workspace"
    open_workspace(directory).close()
    other = sqlite3.connect(directory / "workspace.db")
    other.execute("BEGIN IMMEDIATE")
    try:
        conflux = start_conflux("--no-browser", workspace=directory)
        exit_code = conflux.process.wait(timeout=30)
    finally:
        other.rollback()
        other.close()

    output = conflux.all_output()
    assert exit_code != 0
    assert "busy" in output
    assert "Traceback" not in output


def test_a_database_error_at_startup_gives_a_clear_message(
    start_conflux: StartConflux, tmp_path: Path
) -> None:
    # Found verifying T08: only "busy" had a clear message here, and anything else a stack trace.
    directory = tmp_path / "workspace"
    workspace = open_workspace(directory)
    start_profile_run(workspace.engine, scope={}, mode="full")
    with workspace.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TRIGGER forced_failure BEFORE UPDATE ON profile_run
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
                """
            )
        )
    workspace.close()

    conflux = start_conflux("--no-browser", workspace=directory)
    exit_code = conflux.process.wait(timeout=30)

    output = conflux.all_output()
    assert exit_code != 0
    assert "Traceback" not in output
    assert "Can't finish opening this workspace" in output
    assert "forced failure" in output


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed"])
def test_a_run_ends_with_its_status_and_end_time(
    workspace: Workspace, status: str
) -> None:
    # ADR 0002 point 3.
    run = start_profile_run(workspace.engine, scope={}, mode="full")

    finish_profile_run(workspace.engine, run, status)

    stored = read_profile_run(workspace.engine, run)
    assert stored.status == status
    assert stored.ended_at is not None


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed"])
def test_a_finished_run_is_left_alone_at_the_next_start(
    workspace: Workspace, status: str
) -> None:
    # CFX-APP-first-slice/AC-43: only a run still `running` was interrupted.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    finish_profile_run(workspace.engine, run, status)
    before = read_profile_run(workspace.engine, run)

    mark_interrupted_runs(workspace.engine)

    assert read_profile_run(workspace.engine, run) == before


def test_an_interrupted_run_keeps_what_it_wrote(
    workspace: Workspace, dataset_id: str
) -> None:
    # CFX-APP-first-slice/AC-43: ADR 0002 point 3 keeps partial runs visible in history.
    run, finding = run_with_finding(workspace, dataset_id)

    mark_interrupted_runs(workspace.engine)

    assert len(read_observations(workspace.engine, run)) == 1
    assert read_finding(workspace.engine, finding).profile_run_id == run


@pytest.mark.parametrize("status", ["cancelled", "failed", None, "interrupted"])
def test_findings_ignore_a_run_that_did_not_complete(
    workspace: Workspace, dataset_id: str, status: str | None
) -> None:
    # CFX-APP-first-slice/AC-43, and None is a run still going. ADR 0002 point 3.
    run, _ = run_with_finding(workspace, dataset_id)
    if status == "interrupted":
        mark_interrupted_runs(workspace.engine)
    elif status is not None:
        finish_profile_run(workspace.engine, run, status)

    assert read_findings(workspace.engine) == []


def test_findings_are_read_from_completed_runs(
    workspace: Workspace, dataset_id: str
) -> None:
    # CFX-APP-first-slice/AC-43: the other half, or returning nothing would pass the test above.
    run, finding = run_with_finding(workspace, dataset_id)
    finish_profile_run(workspace.engine, run, "completed")
    run_with_finding(workspace, dataset_id)

    assert read_findings(workspace.engine) == [read_finding(workspace.engine, finding)]


def test_a_run_ends_only_once(workspace: Workspace) -> None:
    # CFX-APP-first-slice/AC-42: a ProfileRun changes only its status and end time, once.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    finish_profile_run(workspace.engine, run, "completed")
    before = read_profile_run(workspace.engine, run)

    with pytest.raises(ValueError, match="already ended"):
        finish_profile_run(workspace.engine, run, "failed")

    assert read_profile_run(workspace.engine, run) == before


def test_an_interrupted_run_cannot_end_again(workspace: Workspace) -> None:
    # CFX-APP-first-slice/AC-42
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    mark_interrupted_runs(workspace.engine)
    before = read_profile_run(workspace.engine, run)

    with pytest.raises(ValueError, match="already ended"):
        finish_profile_run(workspace.engine, run, "completed")

    assert read_profile_run(workspace.engine, run) == before


@pytest.mark.parametrize("status", ["running", "interrupted", "paused"])
def test_a_run_can_only_end_as_completed_cancelled_or_failed(
    workspace: Workspace, status: str
) -> None:
    # ADR 0002 point 3. Only startup marks a run interrupted, when nothing can still be writing it.
    run = start_profile_run(workspace.engine, scope={}, mode="full")

    with pytest.raises(ValueError, match="can't end as"):
        finish_profile_run(workspace.engine, run, status)

    assert read_profile_run(workspace.engine, run).status == "running"


def test_ending_an_unknown_run_is_refused(workspace: Workspace) -> None:
    with pytest.raises(ValueError, match="no such profile run"):
        finish_profile_run(workspace.engine, "nothing", "completed")


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed", "interrupted"])
def test_a_finished_run_takes_no_more_observations(
    workspace: Workspace, dataset_id: str, status: str
) -> None:
    # Found reviewing T07: a Profile is an immutable snapshot per run (ADR 0002 point 4).
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id))
    if status == "interrupted":
        mark_interrupted_runs(workspace.engine)
    else:
        finish_profile_run(workspace.engine, run, status)

    with pytest.raises(ValueError, match="isn't running"):
        add_observations(
            workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id)
        )

    assert len(read_observations(workspace.engine, run)) == 1


@pytest.mark.parametrize("ending", ["cancelled", "failed", "interrupted"])
def test_a_run_that_did_not_complete_takes_no_findings(
    workspace: Workspace, dataset_id: str, ending: str
) -> None:
    # Found reviewing T08: read_findings would never return one, so the write would seem to work.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id))
    observation = read_observations(workspace.engine, run)[0].id
    if ending == "interrupted":
        mark_interrupted_runs(workspace.engine)
    else:
        finish_profile_run(workspace.engine, run, ending)

    with pytest.raises(ValueError, match="didn't complete"):
        add_finding(
            workspace.engine,
            Actor.SYSTEM,
            NewFinding(run, (observation,), "Few rows", "Too few", "Check the export", kind="missing_values"),
        )

    with workspace.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM finding")).scalar() == 0


def test_a_completed_run_still_takes_findings(
    workspace: Workspace, dataset_id: str
) -> None:
    # The other half: an AI finding can come after the run it reads.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id))
    observation = read_observations(workspace.engine, run)[0].id
    finish_profile_run(workspace.engine, run, "completed")

    finding = add_finding(
        workspace.engine,
        Actor.SYSTEM,
        NewFinding(run, (observation,), "Few rows", "Too few", "Check the export", kind="missing_values"),
    )

    assert read_findings(workspace.engine) == [read_finding(workspace.engine, finding)]


def test_a_finding_keeps_its_kind(workspace: Workspace, dataset_id: str) -> None:
    # CFX-APP-csv-profile/AC-21: findings are listed in a fixed order by kind.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id))
    observation = read_observations(workspace.engine, run)[0].id

    finding = add_finding(
        workspace.engine,
        Actor.SYSTEM,
        NewFinding(
            run,
            (observation,),
            "Possible identifier",
            "It may be the key",
            "Confirm it",
            kind="possible_identifier",
        ),
    )

    assert read_finding(workspace.engine, finding).kind == "possible_identifier"


def test_a_finding_of_an_unknown_kind_is_refused(
    workspace: Workspace, dataset_id: str
) -> None:
    # CFX-APP-csv-profile/AC-21: the fixed order only knows the six kinds.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id))
    observation = read_observations(workspace.engine, run)[0].id

    with pytest.raises(IntegrityError):
        add_finding(
            workspace.engine,
            Actor.SYSTEM,
            NewFinding(run, (observation,), "Odd", "Odd", "Odd", kind="interesting"),
        )

    assert read_findings(workspace.engine) == []


def test_findings_are_saved_only_with_a_completed_ending(
    workspace: Workspace, dataset_id: str
) -> None:
    # CFX-APP-csv-profile/AC-26: found reviewing T08, no test held this.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id))
    observation = read_observations(workspace.engine, run)[0].id
    found = NewFinding(run, (observation,), "Odd", "Odd", "Odd", kind="missing_values")

    with pytest.raises(ValueError, match="Only a completed run takes findings"):
        finish_profile_run(workspace.engine, run, "cancelled", [found])

    assert read_profile_run(workspace.engine, run).status == "running"
    with workspace.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM finding")).scalar() == 0


def test_a_run_ends_only_with_its_own_findings(workspace: Workspace, dataset_id: str) -> None:
    # CFX-APP-csv-profile/AC-26: found reviewing T08. Nothing of the ending is kept.
    other = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, other, one_observation(dataset_id))
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    # It cites its own run's observation, so only this guard stops it.
    observation = read_observations(workspace.engine, other)[0].id
    stray = NewFinding(other, (observation,), "Odd", "Odd", "Odd", kind="missing_values")

    with pytest.raises(ValueError, match="A finding must be from the run it ends with"):
        finish_profile_run(workspace.engine, run, "completed", [stray])

    assert read_profile_run(workspace.engine, run).status == "running"
    with workspace.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM finding")).scalar() == 0


def test_a_finding_cannot_list_an_observation_twice(
    workspace: Workspace, dataset_id: str
) -> None:
    # Found reviewing T08: this reached the database as a raw IntegrityError.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id))
    observation = read_observations(workspace.engine, run)[0].id

    with pytest.raises(ValueError, match="twice"):
        add_finding(
            workspace.engine,
            Actor.SYSTEM,
            NewFinding(run, (observation, observation), "Few rows", "Too few", "Check", kind="missing_values"),
        )


def test_a_finding_keeps_its_observations_in_order(
    workspace: Workspace, dataset_id: str
) -> None:
    # Found reviewing T08: no test gave a finding two. Both orders, so sorting can't pass by luck.
    run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(
        workspace.engine, Actor.SYSTEM, run, one_observation(dataset_id) * 2
    )
    first, second = sorted(o.id for o in read_observations(workspace.engine, run))

    for order in ((first, second), (second, first)):
        finding = add_finding(
            workspace.engine,
            Actor.SYSTEM,
            NewFinding(run, order, "Few rows", "Too few", "Check the export", kind="missing_values"),
        )

        assert read_finding(workspace.engine, finding).observation_ids == order


def test_a_finding_needs_an_observation(workspace: Workspace) -> None:
    # Found reviewing T07: Section 12 lists a finding's linked observations.
    run = start_profile_run(workspace.engine, scope={}, mode="full")

    with pytest.raises(ValueError, match="at least one observation"):
        add_finding(
            workspace.engine,
            Actor.SYSTEM,
            NewFinding(run, (), "Few rows", "Too few", "Check the export", kind="missing_values"),
        )


@pytest.mark.parametrize("kind", ["from another run", "unknown"])
def test_a_findings_observations_must_be_from_its_own_run(
    workspace: Workspace, dataset_id: str, kind: str
) -> None:
    # Found reviewing T07.
    other_run = start_profile_run(workspace.engine, scope={}, mode="full")
    add_observations(
        workspace.engine, Actor.SYSTEM, other_run, one_observation(dataset_id)
    )
    other = read_observations(workspace.engine, other_run)[0].id
    run = start_profile_run(workspace.engine, scope={}, mode="full")

    with pytest.raises(ValueError, match="from its own run"):
        add_finding(
            workspace.engine,
            Actor.SYSTEM,
            NewFinding(
                run,
                (other if kind == "from another run" else "nothing",),
                "Few rows",
                "Too few",
                "Check the export",
                kind="missing_values",
            ),
        )

    with workspace.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM finding")).scalar() == 0


def test_a_run_cant_start_on_settings_a_correction_just_replaced(
    workspace: Workspace, dataset_id: str
) -> None:
    # CFX-APP-csv-profile/AC-06, the other way round: a correction saved between reading the
    # settings and starting the run.
    source = find_source(workspace.engine, "source-1")
    assert source is not None
    save_corrected_settings(workspace.engine, replace(source, has_header=False))

    with pytest.raises(SettingsChanged):
        start_only_profile_run(workspace.engine, scope={"dataset_id": dataset_id}, mode="fast")

    with workspace.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM profile_run")).scalar() == 0
