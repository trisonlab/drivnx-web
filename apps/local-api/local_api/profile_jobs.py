"""A profile run's work, done in the background."""

import os
import threading
from itertools import batched
from pathlib import Path
from typing import Any

import duckdb
from artifacts.profiles import (
    MAX_OBSERVATIONS_PER_TRANSACTION,
    NewObservation,
    add_observations,
    finish_profile_run,
    start_only_profile_run,
)
from artifacts.sources import StoredDataset, StoredSource
from artifacts.store import WorkspaceBusy
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError, RowNotReadable, duckdb_connection, load_rows
from domain.trust import Actor
from profiling.first_rows import FIRST_ROWS, FirstRows, measure_first_rows

FIRST_ROWS_TABLE = "first_rows"
JOB_THREAD_NAME = "conflux-profile-job"
# The queries measured peak at about 3 GB here (ADR 0004). DuckDB's default of 80% of the machine's
# memory could slow the user's other programs.
PROFILE_MEMORY_LIMIT = "4GB"
COLUMNS_CHANGED = "The file's columns no longer match the saved ones."
FILE_GONE = "Conflux can't find this file. It may have moved or been deleted."
FILE_CHANGED = "The file changed while it was being profiled. Profile it again."
NO_PERMISSION = "Conflux doesn't have permission to read this file."
COULDNT_READ = "Conflux couldn't read this file."
COULDNT_PROFILE = "Conflux couldn't profile this file."
OUT_OF_MEMORY = "Conflux ran out of memory profiling this file."
ROW_NOT_READABLE = (
    "A row in this file can't be read, so it can't be profiled without leaving that row out."
)
# Why each run failed since Conflux started, written for the user. Never DuckDB's text, which can
# quote a row.
_failures: dict[str, str] = {}


def start_fast_profile(
    workspace: Workspace,
    source: StoredSource,
    dataset: StoredDataset,
    encoding: str,
    stat: os.stat_result,
    modified_at: str,
) -> str:
    """Record the run, then work in the background. Returns its ID.

    Raises `RunInProgress`, or `SettingsChanged` if a correction has replaced `dataset`.
    """
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
        mode="fast",
        sample_size=FIRST_ROWS,
        strategy="first_rows",
    )
    threading.Thread(
        target=_run_fast_profile,
        args=(workspace, run_id, source, encoding, dataset, stat),
        name=JOB_THREAD_NAME,
        daemon=True,
    ).start()
    return run_id


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
    """Why a run failed, if it failed since Conflux started."""
    return _failures.get(run_id)


def _run_fast_profile(
    workspace: Workspace,
    run_id: str,
    source: StoredSource,
    encoding: str,
    dataset: StoredDataset,
    started_on: os.stat_result,
) -> None:
    # In `finally`, so even a mistake in Conflux can't leave a run refusing every later one.
    status = "failed"
    try:
        measured, extra_columns, skipped_lines = _measure(
            workspace, source, encoding, dataset
        )
        # What was measured must still be the file.
        if not _same_file(started_on, source.path.stat()):
            raise FileChanged(FILE_CHANGED)
        for batch in batched(
            _observations(dataset, measured, extra_columns, skipped_lines),
            MAX_OBSERVATIONS_PER_TRANSACTION,
        ):
            add_observations(workspace.engine, Actor.SYSTEM, run_id, batch)
        status = "completed"
    except (CsvError, duckdb.Error, OSError, WorkspaceBusy) as error:
        # DuckDB's message can quote a row from the file, so this must not travel any further.
        _failures[run_id] = _why_it_failed(error, source.path, started_on)
    finally:
        finish_profile_run(workspace.engine, run_id, status)


def _measure(
    workspace: Workspace,
    source: StoredSource,
    encoding: str,
    dataset: StoredDataset,
) -> tuple[FirstRows, int, int]:
    """What was measured, how many columns past the saved ones weren't, and lines skipped."""
    with duckdb_connection(workspace.temp_directory) as connection:
        connection.execute(f"SET memory_limit = '{PROFILE_MEMORY_LIMIT}'")
        loaded = load_rows(
            connection,
            source.path,
            encoding,
            source.delimiter,
            source.has_header,
            source.skip_lines,
            FIRST_ROWS_TABLE,
            FIRST_ROWS + 1,
        )
        # Observations are matched to fields by position, so they mustn't land on the wrong field.
        # A row wider than the rest reads as extra columns after these, with no field to point at.
        saved = [field.name for field in dataset.fields]
        # With no header the names are generated, and past 10 columns DuckDB renumbers them, so they
        # say nothing. Too few columns fails when they're measured.
        if source.has_header and loaded.names[: len(saved)] != saved:
            raise CsvError(COLUMNS_CHANGED)
        measured = measure_first_rows(
            connection,
            FIRST_ROWS_TABLE,
            [field.normalised_type for field in dataset.fields],
            loaded.formats,
        )
        return measured, max(len(loaded.names) - len(saved), 0), loaded.skipped_lines


def _observations(
    dataset: StoredDataset, measured: FirstRows, extra_columns: int, skipped_lines: int
) -> list[NewObservation]:
    scope = "full" if measured.whole_file else "sample"

    def observation(field_id: str | None, metric: str, value: Any) -> NewObservation:
        return NewObservation(dataset.id, field_id, metric, value, scope, "count", "exact")

    return [
        observation(None, "rows_read", measured.rows_read),
        *([observation(None, "extra_columns", extra_columns)] if extra_columns else []),
        *([observation(None, "skipped_lines", skipped_lines)] if skipped_lines else []),
        *(
            observation(field.id, metric, value)
            for field, column in zip(dataset.fields, measured.columns, strict=True)
            for metric, value in column.saved_metrics().items()
        ),
    ]
