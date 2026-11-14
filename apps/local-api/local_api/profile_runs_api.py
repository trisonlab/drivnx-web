"""`/api/profile-runs`: starting a profile run, reading its status and results, and its columns'
values on request."""

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb
from artifacts.profiles import (
    ListedProfileRun,
    RunInProgress,
    SettingsChanged,
    find_listed_profile_run,
    list_profile_runs,
    read_findings,
    read_observations,
    read_profile_run,
)
from artifacts.provenance import now
from artifacts.sources import (
    StoredPostgresSource,
    StoredSource,
    find_source,
    read_dataset,
    read_field_names,
)
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError, read_page, sniffed_columns
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import NoResultFound

from local_api.profile_jobs import (
    Busy,
    Mode,
    cancel_profile,
    failure_of,
    start_profile,
    values_of,
)
from local_api.sources_api import (
    CellResponse,
    cannot_read,
    current_encoding,
    is_available,
    modified_at,
    shown_cell,
)

RUN_IN_PROGRESS = "A profile is already running. Wait for it to finish, then try again."
# Profiling a database comes with choosing its tables (DRIVN-49 and DRIVN-51).
NO_DATABASE_PROFILE_YET = (
    "Profiling a database comes with choosing its tables, which isn't available yet."
)
ALREADY_FINISHED = "This profile has already finished."
SETTINGS_CHANGED = "This file's settings changed just now. Start the profile again."
COLUMNS_NO_LONGER_MATCH = (
    "This file's columns no longer match the saved ones. "
    "Open this source's settings and save them to read the columns again."
)
FILE_GONE = "DrivnX can't find this file. It may have moved or been deleted."
NOT_COMPLETED = "This profile hasn't completed, so it has no values to show."
EARLIER_SETTINGS = "This profile used earlier settings. Profile the file again to see its values."
POSSIBLY_SENSITIVE = (
    "This column may hold personal data. Confirm that you want to see its values."
)
BUSY = "DrivnX is already reading a file. Wait for it to finish, then try again."


class ProfileRunRequest(BaseModel):
    source_id: str
    mode: Mode


class ProfileRunResponse(BaseModel):
    id: str
    source_id: str
    mode: str
    status: str
    started_at: str
    ended_at: str | None
    current_settings: bool
    # Why it failed, when DrivnX knows. Not kept across a restart.
    failure: str | None


class PatternResponse(BaseModel):
    pattern: str
    count: int
    cut: bool


class MetricResponse(BaseModel):
    column: str | None
    metric: str
    value: int | float | list[PatternResponse]
    scope: str
    precision: str


class ProfileRunDetail(ProfileRunResponse):
    metrics: list[MetricResponse]
    # Whether the file's size or modified time differs from the run's now (AC-37), or `None` when
    # the file can't be looked at.
    file_changed: bool | None


class FrequentResponse(BaseModel):
    value: CellResponse
    count: int


class BucketResponse(BaseModel):
    """Values above `low` up to `high`. The first bucket's `low` is the minimum, and holds it."""

    low: CellResponse
    high: CellResponse
    count: int


class ValuesResponse(BaseModel):
    """Computed now and never saved (ADR 0004). Every value is text, cut as the preview's are."""

    profile_run_id: str
    column: str
    # The part of the file read, as a metric's scope says it.
    scope: str
    rows_read: int
    computed_at: str
    minimum: CellResponse | None
    maximum: CellResponse | None
    frequent: list[FrequentResponse]
    histogram: list[BucketResponse] | None
    mean: CellResponse | None
    median: CellResponse | None
    standard_deviation: CellResponse | None


def _shown(value: str | None) -> CellResponse | None:
    return None if value is None else shown_cell(value)


@contextmanager
def _reading() -> Iterator[None]:
    """A file that can't be read, as a message for the user. DuckDB's text never travels."""
    try:
        yield
    except CsvError as error:
        raise cannot_read(str(error)) from None
    except PermissionError:
        raise cannot_read("DrivnX doesn't have permission to read this file.") from None
    except (OSError, duckdb.Error):
        raise cannot_read("DrivnX couldn't read this file.") from None


def _response(run: ListedProfileRun) -> ProfileRunResponse:
    return ProfileRunResponse(
        id=run.id,
        source_id=run.source_id,
        mode=run.mode,
        status=run.status,
        started_at=run.started_at,
        ended_at=run.ended_at,
        current_settings=run.current_settings,
        failure=failure_of(run.id) if run.status == "failed" else None,
    )


def _file_changed(source: StoredSource | None, scope: dict) -> bool | None:
    if source is None:
        return None
    try:
        stat = source.path.stat()
    except OSError:
        return None
    return (stat.st_size, modified_at(stat)) != (scope["size_bytes"], scope["modified_at"])


def columns_now(
    workspace: Workspace, source: StoredSource, encoding: str
) -> list[str] | None:
    """The file's columns, read the way the saved ones were, or `None` if they can't be."""
    if source.corrected:
        # Saved settings take the preview's columns, which include a wider row's extra one.
        return read_page(
            source.path,
            encoding,
            source.delimiter,
            source.has_header,
            0,
            1,
            workspace.temp_directory,
            source.skip_lines,
        ).columns
    return sniffed_columns(
        source.path,
        encoding,
        source.delimiter,
        source.has_header,
        source.skip_lines,
        workspace.temp_directory,
    )


def profile_runs_router(workspace: Workspace) -> APIRouter:
    router = APIRouter(prefix="/api/profile-runs")

    def existing_run(run_id: str) -> ListedProfileRun:
        run = find_listed_profile_run(workspace.engine, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="There's no such profile run.")
        return run

    @router.post("", status_code=202)
    def start_run(body: ProfileRunRequest) -> ProfileRunResponse:
        no_such_source = HTTPException(status_code=404, detail="There's no such source.")
        if isinstance(find_source(workspace.engine, body.source_id), StoredPostgresSource):
            raise HTTPException(status_code=409, detail=NO_DATABASE_PROFILE_YET)
        try:
            # Before the settings, so a correction saved after either replaces this dataset, and
            # the start is refused instead of reading with settings that aren't this dataset's.
            dataset = read_dataset(workspace.engine, body.source_id)
        except NoResultFound:
            raise no_such_source from None
        source = find_source(workspace.engine, body.source_id)
        if source is None:
            raise no_such_source
        if not is_available(source.path):
            raise cannot_read(FILE_GONE)
        with _reading():
            encoding = current_encoding(source)
            stat = source.path.stat()
            columns = columns_now(workspace, source, encoding)
        if columns is not None and columns != [field.name for field in source.fields]:
            raise cannot_read(COLUMNS_NO_LONGER_MATCH)
        try:
            run_id = start_profile(
                workspace, body.mode, source, dataset, encoding, stat, modified_at(stat)
            )
        except RunInProgress:
            raise HTTPException(status_code=409, detail=RUN_IN_PROGRESS) from None
        except Busy:
            raise HTTPException(status_code=409, detail=BUSY) from None
        except SettingsChanged:
            raise HTTPException(status_code=409, detail=SETTINGS_CHANGED) from None
        return _response(existing_run(run_id))

    @router.post("/{run_id}/cancel", status_code=202)
    def cancel_run(run_id: str) -> ProfileRunResponse:
        """The job stops and ends the run as `cancelled` within moments (AC-10)."""
        existing_run(run_id)
        if not cancel_profile(run_id):
            raise HTTPException(status_code=409, detail=ALREADY_FINISHED)
        return _response(existing_run(run_id))

    @router.get("")
    def get_runs() -> list[ProfileRunResponse]:
        return [_response(run) for run in list_profile_runs(workspace.engine)]

    @router.get("/{run_id}")
    def get_run(run_id: str) -> ProfileRunDetail:
        run = existing_run(run_id)
        # A run that hasn't completed may hold only some of its results.
        observations = (
            read_observations(workspace.engine, run.id)
            if run.status == "completed"
            else []
        )
        names = read_field_names(workspace.engine, run.dataset_id)
        scope = read_profile_run(workspace.engine, run.id).scope
        return ProfileRunDetail(
            **_response(run).model_dump(),
            file_changed=_file_changed(find_source(workspace.engine, run.source_id), scope),
            metrics=[
                MetricResponse(
                    column=names[observation.field_id]
                    if observation.field_id
                    else None,
                    metric=observation.metric,
                    value=observation.value,
                    scope=observation.scope,
                    precision=observation.precision,
                )
                for observation in observations
            ],
        )

    def flagged(run_id: str, field_id: str) -> bool:
        """Whether the run found the column possibly sensitive."""
        fields = {seen.id: seen.field_id for seen in read_observations(workspace.engine, run_id)}
        return any(
            fields.get(cited) == field_id
            for finding in read_findings(workspace.engine, run_id)
            if finding.kind == "possibly_sensitive"
            for cited in finding.observation_ids
        )

    @router.get("/{run_id}/values")
    def get_values(run_id: str, column: str, confirmed: bool = False) -> ValuesResponse:
        """A column's values from the part of the file the run read (AC-30), computed now."""
        run = existing_run(run_id)
        if run.status != "completed":
            raise HTTPException(status_code=409, detail=NOT_COMPLETED)
        # Its columns may have been renamed since, and its settings no longer read the file.
        if not run.current_settings:
            raise HTTPException(status_code=409, detail=EARLIER_SETTINGS)
        dataset = read_dataset(workspace.engine, run.source_id)
        position = next(
            (at for at, field in enumerate(dataset.fields) if field.name == column), None
        )
        if position is None:
            raise HTTPException(status_code=404, detail="There's no such column in this profile.")
        # Before the file is read, so a refusal shows nothing (AC-29).
        if not confirmed and flagged(run.id, dataset.fields[position].id):
            raise HTTPException(status_code=409, detail=POSSIBLY_SENSITIVE)
        source = find_source(workspace.engine, run.source_id)
        if source is None or not is_available(source.path):
            raise cannot_read(FILE_GONE)
        with _reading():
            encoding = current_encoding(source)
            columns = columns_now(workspace, source, encoding)
            if columns is not None and columns != [field.name for field in source.fields]:
                raise cannot_read(COLUMNS_NO_LONGER_MATCH)
            try:
                found = values_of(
                    workspace, source, encoding, dataset, position, run.mode == "full"
                )
            except Busy:
                raise HTTPException(status_code=409, detail=BUSY) from None
        return ValuesResponse(
            profile_run_id=run.id,
            column=column,
            scope="full" if found.whole_file else "sample",
            rows_read=found.rows_read,
            computed_at=now(),
            minimum=_shown(found.minimum),
            maximum=_shown(found.maximum),
            frequent=[
                FrequentResponse(value=shown_cell(frequent.value), count=frequent.count)
                for frequent in found.frequent
            ],
            histogram=None
            if found.histogram is None
            else [
                BucketResponse(
                    low=shown_cell(bucket.low), high=shown_cell(bucket.high), count=bucket.count
                )
                for bucket in found.histogram
            ],
            mean=_shown(found.mean),
            median=_shown(found.median),
            standard_deviation=_shown(found.standard_deviation),
        )

    return router
