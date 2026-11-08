"""A profile run's work, done in the background."""

import dataclasses
import os
import threading
import time
from itertools import batched
from pathlib import Path
from typing import Any, Literal

import duckdb
from artifacts.profiles import (
    MAX_OBSERVATIONS_PER_TRANSACTION,
    NewFinding,
    NewObservation,
    add_observations,
    finish_profile_run,
    read_observations,
    start_only_profile_run,
)
from artifacts.sources import StoredDataset, StoredSource
from artifacts.store import WorkspaceBusy
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError, RowNotReadable, duckdb_connection, load_rows
from domain.trust import Actor
from profiling.findings import findings_for
from profiling.first_rows import FIRST_ROWS, MeasuredRows, measure_rows
from profiling.on_request import ColumnValues, values_for

ROWS_TABLE = "loaded_rows"
VALUES_TABLE = "rows_asked_for"
JOB_THREAD_NAME = "drivnx-profile-job"
INTERRUPTING_THREAD_NAME = "drivnx-profile-stop"
# How long shutting down waits for jobs to stop, which an interrupt does within moments.
SHUTDOWN_WAIT_SECONDS = 5
# The queries measured peak at about 3 GB here (ADR 0004). DuckDB's default of 80% of the machine's
# memory could slow the user's other programs.
PROFILE_MEMORY_LIMIT = "4GB"
COLUMNS_CHANGED = "The file's columns no longer match the saved ones."
FILE_GONE = "DrivnX can't find this file. It may have moved or been deleted."
FILE_CHANGED = "The file changed while it was being profiled. Profile it again."
NO_PERMISSION = "DrivnX doesn't have permission to read this file."
COULDNT_READ = "DrivnX couldn't read this file."
COULDNT_PROFILE = "DrivnX couldn't profile this file."
OUT_OF_MEMORY = "DrivnX ran out of memory profiling this file."
ROW_NOT_READABLE = (
    "A row in this file can't be read, so it can't be profiled without leaving that row out."
)
# Why each run failed since DrivnX started, written for the user. Never DuckDB's text, which can
# quote a row.
_failures: dict[str, str] = {}
# An interrupt between two queries is lost, so a cancel repeats it this often until the job ends.
INTERRUPT_EVERY_SECONDS = 0.05


@dataclasses.dataclass
class _Job:
    stop: threading.Event = dataclasses.field(default_factory=threading.Event)
    ended: threading.Event = dataclasses.field(default_factory=threading.Event)
    # Stopped because DrivnX is shutting down, so the run is left for the next start to mark
    # `interrupted`, as the user didn't cancel it.
    leaving: bool = False
    thread: threading.Thread | None = None
    # Set while the job's DuckDB connection is open, and only under `_jobs_lock`.
    connection: duckdb.DuckDBPyConnection | None = None


# The jobs that haven't ended, by run ID.
_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()
# Held while values are worked out on request, as two DuckDB jobs could take twice the memory.
_values_lock = threading.Lock()


class Busy(Exception):
    """A profile is running, or other values are being worked out."""


Mode = Literal["fast", "full"]


def start_profile(
    workspace: Workspace,
    mode: Mode,
    source: StoredSource,
    dataset: StoredDataset,
    encoding: str,
    stat: os.stat_result,
    modified_at: str,
) -> str:
    """Record the run, then work in the background. Returns its ID.

    Raises `RunInProgress`, `Busy` while values are being worked out, or `SettingsChanged` if a
    correction has replaced `dataset`.
    """
    job = _Job()
    # Under the lock that a cancel takes, as the run can be seen, and cancelled, once it's saved.
    with _jobs_lock:
        # `values_of` takes its lock before it looks at the jobs, so one of the two always waits.
        if _values_lock.locked():
            raise Busy
        run_id = start_only_profile_run(
            workspace.engine,
            scope={
                "dataset_id": dataset.id,
                "delimiter": source.delimiter,
                "encoding": encoding,
                "has_header": source.has_header,
                "skip_lines": source.skip_lines,
                "size_bytes": stat.st_size,
                "modified_at": modified_at,
            },
            mode=mode,
            sample_size=FIRST_ROWS if mode == "fast" else None,
            strategy="first_rows" if mode == "fast" else None,
        )
        job.thread = threading.Thread(
            target=_run_profile,
            args=(workspace, run_id, job, mode, source, encoding, dataset, stat),
            name=JOB_THREAD_NAME,
            daemon=True,
        )
        _jobs[run_id] = job
    job.thread.start()
    return run_id


def cancel_profile(run_id: str) -> bool:
    """Ask a running job to stop, and it ends the run as `cancelled`. False if it has ended."""
    with _jobs_lock:
        job = _jobs.get(run_id)
        if job is None:
            return False
        job.stop.set()
    _interrupt(job)
    return True


def stop_jobs() -> None:
    """For shutting down: stop every job, leaving its run `running` for the next start to mark."""
    with _jobs_lock:
        jobs = list(_jobs.values())
        for job in jobs:
            job.leaving = True
            job.stop.set()
    for job in jobs:
        _interrupt(job)
    deadline = time.monotonic() + SHUTDOWN_WAIT_SECONDS
    for job in jobs:
        assert job.thread is not None
        job.thread.join(max(deadline - time.monotonic(), 0))


def _interrupt(job: _Job) -> None:
    threading.Thread(
        target=_interrupt_until_ended, args=(job,), name=INTERRUPTING_THREAD_NAME, daemon=True
    ).start()


def _interrupt_until_ended(job: _Job) -> None:
    while not job.ended.is_set():
        with _jobs_lock:
            # Under the lock, so the job can't close the connection meanwhile.
            if job.connection is not None:
                job.connection.interrupt()
        job.ended.wait(INTERRUPT_EVERY_SECONDS)


class FileChanged(CsvError):
    """The file isn't the one the run started on."""


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    def identity(stat: os.stat_result) -> tuple[int, ...]:
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    return identity(before) == identity(after)


def _why_it_failed(error: Exception, path: Path, started_on: os.stat_result) -> str:
    """Found by looking at the file again, as DuckDB's errors can't be read for a reason."""
    if isinstance(error, WorkspaceBusy):
        # Written for the user, and the file is fine.
        return str(error)
    try:
        # Compared before opening, as opening a pipe put in its place would wait for a writer.
        if not _same_file(started_on, path.stat()):
            return FILE_CHANGED
        with path.open("rb"):
            pass
    except FileNotFoundError:
        return FILE_GONE
    except PermissionError:
        return NO_PERMISSION
    except OSError:
        return COULDNT_READ
    if isinstance(error, duckdb.OutOfMemoryException):
        return OUT_OF_MEMORY
    if isinstance(error, RowNotReadable):
        return ROW_NOT_READABLE
    return COULDNT_PROFILE


def failure_of(run_id: str) -> str | None:
    """Why a run failed, if it failed since DrivnX started."""
    return _failures.get(run_id)


def _run_profile(
    workspace: Workspace,
    run_id: str,
    job: _Job,
    mode: Mode,
    source: StoredSource,
    encoding: str,
    dataset: StoredDataset,
    started_on: os.stat_result,
) -> None:
    # In `finally`, so even a mistake in DrivnX can't leave a run refusing every later one.
    status = "failed"
    findings: list[NewFinding] = []
    try:
        measured, extra_columns, skipped_lines = _measure(
            workspace, job, mode, source, encoding, dataset
        )
        # What was measured must still be the file.
        if not _same_file(started_on, source.path.stat()):
            raise FileChanged(FILE_CHANGED)
        for batch in batched(
            _observations(dataset, measured, extra_columns, skipped_lines),
            MAX_OBSERVATIONS_PER_TRANSACTION,
        ):
            if job.stop.is_set():
                # The run ends as `cancelled` below, so what's saved isn't shown.
                break
            add_observations(workspace.engine, Actor.SYSTEM, run_id, batch)
        # From what was saved, so a finding's evidence is exactly what it cites. A cancelled run
        # may have saved only some of it, and takes no findings.
        findings = [] if job.stop.is_set() else [
            NewFinding(
                run_id,
                draft.observation_ids,
                draft.interpretation,
                draft.why_it_matters,
                draft.recommended_action,
                kind=draft.kind,
            )
            for draft in findings_for(
                read_observations(workspace.engine, run_id),
                {field.id: field.name for field in dataset.fields},
            )
        ]
        status = "completed"
    except (CsvError, duckdb.Error, OSError, WorkspaceBusy) as error:
        # DuckDB's message can quote a row from the file, so this must not travel any further.
        if not job.stop.is_set():
            _failures[run_id] = _why_it_failed(error, source.path, started_on)
    finally:
        # Under the lock that a cancel takes, so a cancel is either refused or what the run ends as.
        with _jobs_lock:
            try:
                if not job.leaving:
                    if job.stop.is_set():
                        status = "cancelled"
                    finish_profile_run(
                        workspace.engine,
                        run_id,
                        status,
                        findings if status == "completed" else (),
                    )
            finally:
                del _jobs[run_id]
                # Also when the ending can't be recorded, or its interrupting would never stop.
                job.ended.set()


def _measure(
    workspace: Workspace,
    job: _Job,
    mode: Mode,
    source: StoredSource,
    encoding: str,
    dataset: StoredDataset,
) -> tuple[MeasuredRows, int, int]:
    """What was measured, how many columns past the saved ones weren't, and lines skipped."""
    with duckdb_connection(workspace.temp_directory) as connection:
        with _jobs_lock:
            job.connection = connection
        try:
            return _measure_with(connection, mode, source, encoding, dataset)
        finally:
            with _jobs_lock:
                job.connection = None


def _measure_with(
    connection: duckdb.DuckDBPyConnection,
    mode: Mode,
    source: StoredSource,
    encoding: str,
    dataset: StoredDataset,
) -> tuple[MeasuredRows, int, int]:
    connection.execute(f"SET memory_limit = '{PROFILE_MEMORY_LIMIT}'")
    loaded = load_rows(
        connection,
        source.path,
        encoding,
        source.delimiter,
        source.has_header,
        source.skip_lines,
        ROWS_TABLE,
        # One more than is measured, to know whether that was the whole file.
        FIRST_ROWS + 1 if mode == "fast" else None,
    )
    # Observations are matched to fields by position, so they mustn't land on the wrong field.
    # A row wider than the rest reads as extra columns after these, with no field to point at.
    saved = [field.name for field in dataset.fields]
    # With no header the names are generated, and past 10 columns DuckDB renumbers them, so they
    # say nothing. Too few columns fails when they're measured.
    if source.has_header and loaded.names[: len(saved)] != saved:
        raise CsvError(COLUMNS_CHANGED)
    measured = measure_rows(
        connection,
        ROWS_TABLE,
        [field.normalised_type for field in dataset.fields],
        loaded.formats,
        every_row=mode == "full",
    )
    return measured, max(len(loaded.names) - len(saved), 0), loaded.skipped_lines


def _observations(
    dataset: StoredDataset, measured: MeasuredRows, extra_columns: int, skipped_lines: int
) -> list[NewObservation]:
    scope = "full" if measured.whole_file else "sample"
    # Patterns only ever come from the first rows (ADR 0004), so a full profile's are a sample too.
    patterns_scope = (
        "full" if measured.whole_file and measured.rows_read <= FIRST_ROWS else "sample"
    )

    def observation(field_id: str | None, metric: str, value: Any) -> NewObservation:
        measured_on = patterns_scope if metric == "masked_patterns" else scope
        return NewObservation(dataset.id, field_id, metric, value, measured_on, "count", "exact")

    return [
        observation(None, "rows_read", measured.rows_read),
        *([observation(None, "extra_columns", extra_columns)] if extra_columns else []),
        *([observation(None, "skipped_lines", skipped_lines)] if skipped_lines else []),
        *(
            [observation(None, "duplicate_rows", measured.duplicate_rows)]
            if measured.duplicate_rows is not None
            else []
        ),
        *(
            observation(field.id, metric, value)
            for field, column in zip(dataset.fields, measured.columns, strict=True)
            for metric, value in column.saved_metrics().items()
        ),
    ]


def values_of(
    workspace: Workspace,
    source: StoredSource,
    encoding: str,
    dataset: StoredDataset,
    position: int,
    every_row: bool,
) -> ColumnValues:
    """A column's values, read as a run reads the file, now, and never saved (ADR 0004).

    Raises `Busy` while a profile runs or other values are being worked out.
    """
    if not _values_lock.acquire(blocking=False):
        raise Busy
    try:
        with _jobs_lock:
            if _jobs:
                raise Busy
        with duckdb_connection(workspace.temp_directory) as connection:
            connection.execute(f"SET memory_limit = '{PROFILE_MEMORY_LIMIT}'")
            loaded = load_rows(
                connection,
                source.path,
                encoding,
                source.delimiter,
                source.has_header,
                source.skip_lines,
                VALUES_TABLE,
                None if every_row else FIRST_ROWS + 1,
            )
            saved = [field.name for field in dataset.fields]
            # As a run checks, so a value can't be taken from the wrong column.
            if source.has_header and loaded.names[: len(saved)] != saved:
                raise CsvError(COLUMNS_CHANGED)
            return values_for(
                connection,
                VALUES_TABLE,
                position,
                dataset.fields[position].normalised_type,
                loaded.formats,
                every_row,
            )
    finally:
        _values_lock.release()
