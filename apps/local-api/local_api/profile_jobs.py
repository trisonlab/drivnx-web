"""A profile run's work, done in the background."""

import os
import threading
from itertools import batched

import duckdb
from artifacts.profiles import (
    MAX_OBSERVATIONS_PER_TRANSACTION,
    NewObservation,
    add_observations,
    finish_profile_run,
    start_only_profile_run,
)
from artifacts.sources import StoredDataset, StoredSource, read_dataset
from artifacts.store import WorkspaceBusy
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError, duckdb_connection, load_rows
from domain.trust import Actor
from profiling.first_rows import FIRST_ROWS, FirstRows, measure_first_rows

FIRST_ROWS_TABLE = "first_rows"
JOB_THREAD_NAME = "conflux-profile-job"
# The queries measured peak at about 3 GB here (ADR 0004). DuckDB's default of 80% of the machine's
# memory could slow the user's other programs.
PROFILE_MEMORY_LIMIT = "4GB"
COLUMNS_CHANGED = "The file's columns no longer match the saved ones."


def start_fast_profile(
    workspace: Workspace,
    source: StoredSource,
    encoding: str,
    stat: os.stat_result,
    modified_at: str,
) -> str:
    """Record the run, then work in the background. Returns its ID, or raises `RunInProgress`."""
    dataset = read_dataset(workspace.engine, source.id)
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
        args=(workspace, run_id, source, encoding, dataset),
        name=JOB_THREAD_NAME,
        daemon=True,
    ).start()
    return run_id


def _run_fast_profile(
    workspace: Workspace,
    run_id: str,
    source: StoredSource,
    encoding: str,
    dataset: StoredDataset,
) -> None:
    # In `finally`, so even a mistake in Conflux can't leave a run refusing every later one.
    status = "failed"
    try:
        measured = _measure(workspace, source, encoding, dataset)
        for batch in batched(
            _observations(dataset, measured), MAX_OBSERVATIONS_PER_TRANSACTION
        ):
            add_observations(workspace.engine, Actor.SYSTEM, run_id, batch)
        status = "completed"
    except (CsvError, duckdb.Error, OSError, WorkspaceBusy):
        # DuckDB's message can quote a row from the file, so this must not travel any further.
        pass
    finally:
        finish_profile_run(workspace.engine, run_id, status)


def _measure(
    workspace: Workspace,
    source: StoredSource,
    encoding: str,
    dataset: StoredDataset,
) -> FirstRows:
    with duckdb_connection(workspace.temp_directory) as connection:
        connection.execute(f"SET memory_limit = '{PROFILE_MEMORY_LIMIT}'")
        columns = load_rows(
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
        if source.has_header and columns[: len(saved)] != saved:
            raise CsvError(COLUMNS_CHANGED)
        return measure_first_rows(connection, FIRST_ROWS_TABLE, len(saved))


def _observations(
    dataset: StoredDataset, measured: FirstRows
) -> list[NewObservation]:
    scope = "full" if measured.whole_file else "sample"
    return [
        NewObservation(
            dataset.id, None, "rows_read", measured.rows_read, scope, "count", "exact"
        ),
        *(
            NewObservation(
                dataset.id, field.id, "missing_count", missing, scope, "count", "exact"
            )
            for field, missing in zip(dataset.fields, measured.missing, strict=True)
        ),
    ]
