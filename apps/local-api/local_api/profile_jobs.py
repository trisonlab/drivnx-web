"""A profile run's work, done in the background."""

import dataclasses
import os
import re
import secrets
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
from artifacts.sources import ChosenObject, StoredDataset, StoredPostgresSource, StoredSource
from artifacts.store import WorkspaceBusy
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError, RowNotReadable, duckdb_connection, load_rows
from connectors.postgres import (
    SAMPLE_ROWS,
    ConnectFailure,
    ConnectionDetails,
    Reading,
    attach,
    load_object_rows,
)
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
    # The object a database's run is reading, as `schema.object` (DRIVN-51). Written by the job
    # alone, and read without the lock (`object_of`).
    object_name: str | None = None


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


def start_database_profile(
    workspace: Workspace,
    details: ConnectionDetails,
    source: StoredPostgresSource,
    password: str,
    objects: tuple[ChosenObject, ...],
    readings: dict[str, Reading],
    *,
    write_access: dict,
) -> str:
    """Record a fast profile of a database's chosen objects, each read as `readings` says by its
    dataset's id, then work in the background. Returns its ID (DRIVN-51).

    Raises `RunInProgress`, `Busy` while values are being worked out, or `SettingsChanged` if a
    refresh has replaced one of the objects.
    """
    job = _Job()
    with _jobs_lock:
        if _values_lock.locked():
            raise Busy
        seed = secrets.randbelow(2**31)
        run_id = start_only_profile_run(
            workspace.engine,
            scope={
                "source_id": source.id,
                "objects": [
                    {
                        "dataset_id": found.dataset.id,
                        "schema": found.schema,
                        "name": found.name,
                        "kind": found.kind,
                        "method": readings[found.dataset.id].method,
                        "percent": readings[found.dataset.id].percent,
                    }
                    for found in objects
                ],
                # What the role could change in them as the run started (DRIVN-52).
                "write_access": write_access,
            },
            mode="fast",
            sample_size=SAMPLE_ROWS,
            # One seed for the run, so its page samples, and values on request, can be repeated.
            seed=seed,
        )
        job.thread = threading.Thread(
            target=_run_database_profile,
            args=(workspace, run_id, job, details, password, objects, readings, seed),
            name=JOB_THREAD_NAME,
            daemon=True,
        )
        _jobs[run_id] = job
    job.thread.start()
    return run_id


def _run_database_profile(
    workspace: Workspace,
    run_id: str,
    job: _Job,
    details: ConnectionDetails,
    password: str,
    objects: tuple[ChosenObject, ...],
    readings: dict[str, Reading],
    seed: int,
) -> None:
    status = "failed"
    try:
        observations = _measure_objects(workspace, job, details, password, objects, readings, seed)
        for batch in batched(observations, MAX_OBSERVATIONS_PER_TRANSACTION):
            if job.stop.is_set():
                break
            add_observations(workspace.engine, Actor.SYSTEM, run_id, batch)
        status = "completed"
    except (ConnectFailure, duckdb.Error, OSError, WorkspaceBusy) as error:
        # Never DuckDB's or the server's text, which can quote the database's values.
        if not job.stop.is_set():
            if isinstance(error, WorkspaceBusy):
                _failures[run_id] = str(error)
            elif job.object_name is None:
                # Refused before any object was read: the connection, not an object.
                _failures[run_id] = _why_object_failed(error)
            else:
                _failures[run_id] = f"{job.object_name}: {_why_object_failed(error)}"
    finally:
        with _jobs_lock:
            try:
                if not job.leaving:
                    if job.stop.is_set():
                        status = "cancelled"
                    finish_profile_run(workspace.engine, run_id, status, ())
            finally:
                del _jobs[run_id]
                job.ended.set()


# The text types whose lengths and masked patterns are measured; any other column gets counts
# only (CFX-APP-postgres, the Assumptions' metrics). An array of them isn't one.
_TEXT_TYPE = re.compile(r"(text|character varying|character|citext|name)(\(\d+\))?")
# Measured for every column. "Values that don't fit the type" can't happen in a typed column.
_COUNTS = ("missing_count", "missing_percent", "distinct_count", "distinct_percent")


def _measure_objects(
    workspace: Workspace,
    job: _Job,
    details: ConnectionDetails,
    password: str,
    objects: tuple[ChosenObject, ...],
    readings: dict[str, Reading],
    seed: int,
) -> list[NewObservation]:
    """Each object read as planned and measured, over one connection to the server."""
    observations: list[NewObservation] = []
    with duckdb_connection(workspace.temp_directory) as connection:
        with _jobs_lock:
            job.connection = connection
        try:
            connection.execute(f"SET memory_limit = '{PROFILE_MEMORY_LIMIT}'")
            attach(connection, details, password)
            for found in objects:
                job.object_name = f"{found.schema}.{found.name}"
                fields = found.dataset.fields
                method = load_object_rows(
                    connection,
                    (found.schema, found.name),
                    tuple(field.name for field in fields),
                    readings[found.dataset.id],
                    seed,
                    ROWS_TABLE,
                )
                measured = measure_rows(
                    connection,
                    ROWS_TABLE,
                    [field.normalised_type for field in fields],
                    {},
                    # Every row read, unless an object turned out larger than its estimate.
                    every_row=method != "first_rows",
                    count_duplicates=False,
                    blank_is_missing=False,
                )
                connection.execute(f"DROP TABLE {ROWS_TABLE}")
                observations += _object_observations(found, measured, method)
        finally:
            with _jobs_lock:
                job.connection = None
    return observations


def _why_object_failed(error: Exception) -> str:
    """Written for the user: the connector's own reason, or DrivnX's."""
    if isinstance(error, ConnectFailure):
        return str(error)
    if isinstance(error, duckdb.OutOfMemoryException):
        return "DrivnX ran out of memory profiling this object."
    return "DrivnX couldn't profile this object."


def _object_observations(
    found: ChosenObject, measured: MeasuredRows, method: str
) -> list[NewObservation]:
    scope = "full" if method == "whole" else "sample"

    def observation(field_id: str | None, metric: str, value: Any) -> NewObservation:
        return NewObservation(found.dataset.id, field_id, metric, value, scope, "count", "exact")

    return [
        observation(None, "rows_read", measured.rows_read),
        *(
            observation(field.id, metric, value)
            for field, column in zip(found.dataset.fields, measured.columns, strict=True)
            for metric, value in column.saved_metrics().items()
            if metric in _COUNTS
            or (metric != "not_fitting_count" and _TEXT_TYPE.fullmatch(field.physical_type))
        ),
    ]


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


def object_of(run_id: str) -> str | None:
    """The object a database's running run is reading, as `schema.object` (AC-17).

    Not under `_jobs_lock`: a run ends holding it while it writes the workspace, and listing the
    runs mustn't wait for that. One lookup and one attribute, which the job alone writes.
    """
    job = _jobs.get(run_id)
    return job.object_name if job else None


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
