"""A profile run's work, done in the background."""

import contextlib
import dataclasses
import functools
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterator
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
    STATEMENT_TIMEOUT_SECONDS,
    ConnectionDetails,
    Reading,
    attach,
    cancel_read,
    load_object_rows,
    load_whole_object,
    reset_whole_read,
    session_pid,
)
from domain.trust import Actor
from profiling.findings import DraftFinding, findings_for
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
# A database's full profile stops an object at this, unless the run asks for another (DRIVN-53).
DEFAULT_TIME_LIMIT_MINUTES = 10
SECONDS_PER_MINUTE = 60
# Saved for an object a full profile stopped at its time limit, with the limit in minutes.
TIME_LIMIT_REACHED = "time_limit_reached"
# Saved on a full profile for the rows its fast reading took, which its masked patterns are from.
SAMPLE_ROWS_READ = "sample_rows_read"
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
# How long a database's read has to return after an interrupt before it's cancelled on the
# server, as a view's read and a `postgres_query` ignore the interrupt (ADR 0009).
STOP_GRACE_SECONDS = 1


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
    # Cancels the read a database's job has running on the server (ADR 0009). Set only under
    # `_jobs_lock`, while the job's connection is attached. It holds the password, so it's left
    # out of the job's repr.
    cancel_on_server: Callable[[], None] | None = dataclasses.field(default=None, repr=False)


# The jobs that haven't ended, by run ID.
_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()
# Held while values are worked out on request, as two DuckDB jobs could take twice the memory.
_values_lock = threading.Lock()


class Busy(Exception):
    """A profile is running, or other values are being worked out."""


class TimeLimitReached(Exception):
    """A full run's values took longer to read than the run's time limit (DRIVN-56)."""

    def __init__(self, minutes: int) -> None:
        super().__init__(minutes)
        self.minutes = minutes


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
    mode: Mode,
    details: ConnectionDetails,
    source: StoredPostgresSource,
    password: str,
    objects: tuple[ChosenObject, ...],
    readings: dict[str, Reading],
    *,
    write_access: dict,
    time_limit_minutes: int | None = None,
    check_connection: bool = False,
) -> str:
    """Record a profile of a database's chosen objects, then work in the background. Returns its ID.

    A fast profile reads each as `readings` says by its dataset's id (DRIVN-51), a full one every
    row (DRIVN-53). With `check_connection`, the server ends its read if DrivnX is killed
    (DRIVN-57), for a server whose discovery `checks_closed_connections`.

    Raises `RunInProgress`, `Busy` while values are being worked out, or `SettingsChanged` if a
    refresh has replaced one of the objects.
    """
    job = _Job()
    time_limit_minutes = time_limit_minutes or DEFAULT_TIME_LIMIT_MINUTES
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
                **({"time_limit_minutes": time_limit_minutes} if mode == "full" else {}),
            },
            mode=mode,
            sample_size=SAMPLE_ROWS if mode == "fast" else None,
            # One seed for the run, so its page samples, and values on request, can be repeated.
            seed=seed,
        )
        job.thread = threading.Thread(
            target=_run_database_profile,
            args=(
                workspace,
                run_id,
                job,
                mode,
                details,
                password,
                objects,
                readings,
                seed,
                time_limit_minutes,
                check_connection,
            ),
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
    mode: Mode,
    details: ConnectionDetails,
    password: str,
    objects: tuple[ChosenObject, ...],
    readings: dict[str, Reading],
    seed: int,
    time_limit_minutes: int,
    check_connection: bool,
) -> None:
    status = "failed"
    findings: list[NewFinding] = []
    try:
        observations = _measure_objects(
            workspace,
            job,
            mode,
            details,
            password,
            objects,
            readings,
            seed,
            time_limit_minutes,
            check_connection,
        )
        for batch in batched(observations, MAX_OBSERVATIONS_PER_TRANSACTION):
            if job.stop.is_set():
                break
            add_observations(workspace.engine, Actor.SYSTEM, run_id, batch)
        # As a CSV's, from what was saved, and none for a cancelled run.
        findings = [] if job.stop.is_set() else _objects_findings(workspace, run_id, objects)
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
                    finish_profile_run(
                        workspace.engine,
                        run_id,
                        status,
                        findings if status == "completed" else (),
                    )
            finally:
                del _jobs[run_id]
                job.ended.set()


def _objects_findings(
    workspace: Workspace, run_id: str, objects: tuple[ChosenObject, ...]
) -> list[NewFinding]:
    """Each object's findings from its saved observations, by object in the run's order. A column
    that is a primary key or unique constraint on its own is no possible identifier (AC-26)."""
    saved = read_observations(workspace.engine, run_id)
    findings = []
    for found in objects:
        keyed = frozenset(
            field.id
            for field in found.dataset.fields
            if any(
                key.kind in ("primary_key", "unique") and key.columns == (field.name,)
                for key in found.keys
            )
        )
        findings += _new_findings(
            run_id,
            findings_for(
                [seen for seen in saved if seen.dataset_id == found.dataset.id],
                {field.id: field.name for field in found.dataset.fields},
                keyed=keyed,
            ),
        )
    return findings


def _new_findings(run_id: str, drafts: list[DraftFinding]) -> list[NewFinding]:
    """The rules' findings, to save with the run."""
    return [
        NewFinding(
            run_id,
            draft.observation_ids,
            draft.interpretation,
            draft.why_it_matters,
            draft.recommended_action,
            kind=draft.kind,
        )
        for draft in drafts
    ]


# The text types whose lengths and masked patterns are measured; any other column gets counts
# only (DRIVN-45, the Assumptions' metrics). An array of them isn't one.
_TEXT_TYPE = re.compile(r"(text|character varying|character|citext|name)(\(\d+\))?")
# Measured for every column. "Values that don't fit the type" can't happen in a typed column.
_COUNTS = ("missing_count", "missing_percent", "distinct_count", "distinct_percent")


def _measure_objects(
    workspace: Workspace,
    job: _Job,
    mode: Mode,
    details: ConnectionDetails,
    password: str,
    objects: tuple[ChosenObject, ...],
    readings: dict[str, Reading],
    seed: int,
    time_limit_minutes: int,
    check_connection: bool,
) -> list[NewObservation]:
    """Each object read as planned and measured, over one connection to the server. On a full
    profile, an object still being read at its time limit is stopped, and the run goes on."""
    observations: list[NewObservation] = []
    with duckdb_connection(workspace.temp_directory) as connection:
        with _jobs_lock:
            job.connection = connection
        try:
            connection.execute(f"SET memory_limit = '{PROFILE_MEMORY_LIMIT}'")
            # A full read is one statement, a view's or a table's chunk, that DrivnX stops at
            # its time limit. The server's limit is a margin past that (ADR 0009 Decision 3).
            attach(
                connection,
                details,
                password,
                None
                if mode == "fast"
                else time_limit_minutes * SECONDS_PER_MINUTE + STATEMENT_TIMEOUT_SECONDS,
                one_session=True,
                check_connection=check_connection,
            )

            def aim_cancel() -> None:
                """Point the server's cancel at the session about to read. The extension
                replaces one that has ended or been left idle a minute, without a word."""
                pid = session_pid(connection)
                with _jobs_lock:
                    job.cancel_on_server = functools.partial(
                        cancel_read, details, password, pid, workspace.temp_directory
                    )

            for found in objects:
                job.object_name = f"{found.schema}.{found.name}"
                aim_cancel()
                if mode == "fast":
                    observations += _read_object(connection, mode, found, readings, seed)
                else:
                    observations += _read_within_time_limit(
                        connection, job, found, readings, seed, time_limit_minutes, aim_cancel
                    )
        finally:
            with _jobs_lock:
                job.connection = None
                job.cancel_on_server = None
    return observations


def _read_object(
    connection: duckdb.DuckDBPyConnection,
    mode: Mode,
    found: ChosenObject,
    readings: dict[str, Reading],
    seed: int,
    before_whole_read: Callable[[], None] = lambda: None,
) -> list[NewObservation]:
    """An object read as `mode` says, and what's saved for it."""
    name = (found.schema, found.name)
    columns = tuple(field.name for field in found.dataset.fields)
    types = [field.normalised_type for field in found.dataset.fields]
    fast = load_object_rows(connection, name, columns, readings[found.dataset.id], seed, ROWS_TABLE)
    sampled = _measure_loaded(connection, types, fast)
    if mode == "fast":
        return _object_observations(found, sampled, fast)
    # A full profile's masked patterns are its fast reading's (decided for DRIVN-53).
    before_whole_read()
    load_whole_object(connection, name, columns, ROWS_TABLE)
    measured = _measure_loaded(connection, types, "full")
    return _object_observations(found, measured, "full", (sampled, fast))


def _read_within_time_limit(
    connection: duckdb.DuckDBPyConnection,
    job: _Job,
    found: ChosenObject,
    readings: dict[str, Reading],
    seed: int,
    time_limit_minutes: int,
    before_whole_read: Callable[[], None],
) -> list[NewObservation]:
    """An object's full profile, or only that it was stopped at its time limit (AC-20)."""
    reached = threading.Event()
    try:
        with _time_limit(job, time_limit_minutes, reached):
            return _read_object(connection, "full", found, readings, seed, before_whole_read)
    except (ConnectFailure, duckdb.Error):
        if job.stop.is_set() or not reached.is_set():
            raise
        # No results for it, only that it was stopped (ADR 0009 Decision 5).
        return [
            NewObservation(
                found.dataset.id,
                None,
                TIME_LIMIT_REACHED,
                time_limit_minutes,
                "full",
                "count",
                "exact",
            )
        ]
    finally:
        if reached.is_set() and not job.stop.is_set():
            # Only once no interrupt can stop these: what the stopped read left behind.
            reset_whole_read(connection)
            connection.execute(f"DROP TABLE IF EXISTS {ROWS_TABLE}")


@contextlib.contextmanager
def _time_limit(job: _Job, minutes: int, reached: threading.Event) -> Iterator[None]:
    """Stop the job's read once `minutes` have passed, setting `reached`. Over only once nothing
    can interrupt the read any more."""
    read_over = threading.Event()
    timer = threading.Timer(
        minutes * SECONDS_PER_MINUTE, _stop_at_time_limit, args=(job, reached, read_over)
    )
    timer.name, timer.daemon = INTERRUPTING_THREAD_NAME, True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        read_over.set()
        timer.join()


def _measure_loaded(
    connection: duckdb.DuckDBPyConnection, types: list[str], method: str
) -> MeasuredRows:
    """Measure the rows just loaded, read as `method` says, then drop them."""
    measured = measure_rows(
        connection,
        ROWS_TABLE,
        types,
        {},
        # Every row read, unless an object turned out larger than its estimate.
        every_row=method != "first_rows",
        # Copies of a row are a full profile's, as for a CSV (DRIVN-53).
        count_duplicates=method == "full",
        blank_is_missing=False,
    )
    connection.execute(f"DROP TABLE {ROWS_TABLE}")
    return measured


def read_scope(method: str) -> str:
    """A metric's scope for rows read as `method` says: all of them, or a sample."""
    return "full" if method in ("whole", "full") else "sample"


def _why_object_failed(error: Exception) -> str:
    """Written for the user: the connector's own reason, or DrivnX's."""
    if isinstance(error, ConnectFailure):
        return str(error)
    if isinstance(error, duckdb.OutOfMemoryException):
        return "DrivnX ran out of memory profiling this object."
    return "DrivnX couldn't profile this object."


def _object_observations(
    found: ChosenObject,
    measured: MeasuredRows,
    method: str,
    patterns_from: tuple[MeasuredRows, str] | None = None,
) -> list[NewObservation]:
    """What's saved for an object read as `method` says, with its masked patterns taken from
    `patterns_from`, rows read another way, when given."""
    patterns_method = method
    if patterns_from is not None:
        sampled, patterns_method = patterns_from
        measured = dataclasses.replace(
            measured,
            columns=tuple(
                dataclasses.replace(column, patterns=sample.patterns)
                for column, sample in zip(measured.columns, sampled.columns, strict=True)
            ),
        )

    def observation(field_id: str | None, metric: str, value: Any) -> NewObservation:
        from_patterns_rows = metric in ("masked_patterns", SAMPLE_ROWS_READ)
        scope = read_scope(patterns_method if from_patterns_rows else method)
        return NewObservation(found.dataset.id, field_id, metric, value, scope, "count", "exact")

    return [
        observation(None, "rows_read", measured.rows_read),
        *(
            [observation(None, SAMPLE_ROWS_READ, patterns_from[0].rows_read)]
            if patterns_from is not None
            else []
        ),
        *(
            [observation(None, "duplicate_rows", measured.duplicate_rows)]
            if measured.duplicate_rows is not None
            else []
        ),
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
        target=_stop_read, args=(job, job.ended), name=INTERRUPTING_THREAD_NAME, daemon=True
    ).start()


def _stop_at_time_limit(job: _Job, reached: threading.Event, read_over: threading.Event) -> None:
    reached.set()
    _stop_read(job, read_over)


def _stop_read(job: _Job, until: threading.Event) -> None:
    """Interrupt the job's read until `until` is set, and cancel it on the server once if that
    takes longer than `STOP_GRACE_SECONDS` (ADR 0009)."""
    cancel_after = time.monotonic() + STOP_GRACE_SECONDS
    cancelled = False
    while not until.is_set():
        cancel = None
        with _jobs_lock:
            # Under the lock, so the job can't close the connection meanwhile.
            if job.connection is not None:
                job.connection.interrupt()
            if not cancelled and time.monotonic() >= cancel_after:
                cancel = job.cancel_on_server
        if cancel is not None:
            # Once, and not under the lock, as it connects to the server.
            cancelled = True
            try:
                cancel()
            except (ConnectFailure, duckdb.Error, OSError):
                # The read still ends at its statement timeout, and the run as asked.
                pass
        until.wait(INTERRUPT_EVERY_SECONDS)


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
        findings = [] if job.stop.is_set() else _new_findings(
            run_id,
            findings_for(
                read_observations(workspace.engine, run_id),
                {field.id: field.name for field in dataset.fields},
            ),
        )
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


@contextlib.contextmanager
def _working_out_values() -> Iterator[None]:
    """Held while values are worked out on request. Raises `Busy` while a profile runs or other
    values are being worked out."""
    if not _values_lock.acquire(blocking=False):
        raise Busy
    try:
        with _jobs_lock:
            if _jobs:
                raise Busy
        yield
    finally:
        _values_lock.release()


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
    with _working_out_values(), duckdb_connection(workspace.temp_directory) as connection:
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


def object_values_of(
    workspace: Workspace,
    details: ConnectionDetails,
    password: str,
    found: ChosenObject,
    position: int,
    mode: Mode,
    reading: Reading,
    seed: int,
    *,
    time_limit_minutes: int = DEFAULT_TIME_LIMIT_MINUTES,
) -> tuple[str, ColumnValues]:
    """A database column's values, read now as its run read the object, and never saved
    (DRIVN-56). Returns how the rows were read, `full` or as `load_object_rows` says, and the
    values.

    Raises `Busy` while a profile runs or other values are being worked out,
    `TimeLimitReached` if a full run's values take longer than its time limit, or the
    connector's `ConnectFailure`.
    """
    # Stopped only at its time limit. It's in no `_jobs`, so no cancel or run list sees it.
    job = _Job()
    name = (found.schema, found.name)
    columns = tuple(field.name for field in found.dataset.fields)
    column_type = found.dataset.fields[position].normalised_type
    with _working_out_values(), duckdb_connection(workspace.temp_directory) as connection:
        with _jobs_lock:
            job.connection = connection
        try:
            connection.execute(f"SET memory_limit = '{PROFILE_MEMORY_LIMIT}'")
            # One session, so a cancel on the server is aimed at the one reading, and a full
            # read's statement limit is a margin past its time limit, as a run's.
            attach(
                connection,
                details,
                password,
                None
                if mode == "fast"
                else time_limit_minutes * SECONDS_PER_MINUTE + STATEMENT_TIMEOUT_SECONDS,
                one_session=True,
            )

            def measured(method: str) -> tuple[str, ColumnValues]:
                # As `_measure_loaded` measured the run's rows.
                every_row = method != "first_rows"
                return method, values_for(
                    connection, VALUES_TABLE, position, column_type, {}, every_row
                )

            if mode == "fast":
                return measured(
                    load_object_rows(connection, name, columns, reading, seed, VALUES_TABLE)
                )
            pid = session_pid(connection)
            with _jobs_lock:
                job.cancel_on_server = functools.partial(
                    cancel_read, details, password, pid, workspace.temp_directory
                )
            reached = threading.Event()
            try:
                # The values too, as a run's measuring is within its limit.
                with _time_limit(job, time_limit_minutes, reached):
                    load_whole_object(connection, name, columns, VALUES_TABLE)
                    return measured("full")
            except (ConnectFailure, duckdb.Error):
                if not reached.is_set():
                    raise
                raise TimeLimitReached(time_limit_minutes) from None
        finally:
            with _jobs_lock:
                job.connection = None
                job.cancel_on_server = None
