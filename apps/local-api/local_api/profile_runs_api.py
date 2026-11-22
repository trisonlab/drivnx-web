"""`/api/profile-runs`: starting a profile run, reading its status and results, and its columns'
values on request."""

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb
from artifacts.profiles import (
    ListedProfileRun,
    Observation,
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
    changed_since_listed,
    find_source,
    read_chosen_objects,
    read_dataset,
    read_field_names,
    read_write_confirmation,
    replaced_datasets,
    save_write_confirmation,
)
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError, read_page, sniffed_columns
from connectors.postgres import ConnectFailure, Reading, discover, reading_for
from fastapi import APIRouter, HTTPException
from profiling.on_request import ColumnValues
from pydantic import BaseModel, Field
from sqlalchemy.exc import NoResultFound

from local_api.profile_jobs import (
    DEFAULT_TIME_LIMIT_MINUTES,
    SAMPLE_ROWS_READ,
    TIME_LIMIT_REACHED,
    Busy,
    Mode,
    TimeLimitReached,
    cancel_profile,
    failure_of,
    object_of,
    object_values_of,
    read_scope,
    start_database_profile,
    start_profile,
    values_of,
)
from local_api.pages import CellResponse, shown_cell
from local_api.postgres_api import (
    KeptPasswords,
    details_of,
    found_objects,
    password_for,
    read_failed,
)
from local_api.sources_api import (
    cannot_read,
    current_encoding,
    is_available,
    modified_at,
)
from local_api.write_access import can_write, refusal, write_access

RUN_IN_PROGRESS = "A profile is already running. Wait for it to finish, then try again."
NOTHING_CHOSEN = "Choose the objects to profile first."
TIME_LIMIT_NOT_HERE = "A time limit applies only to a database's full profile."
OBJECTS_CHANGED = "{} changed since the list was refreshed. Refresh the list, then profile again."
OBJECTS_CHANGED_JUST_NOW = "The list was refreshed just now. Start the profile again."
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
NO_SUCH_OBJECT = "There's no such object in this profile."
NO_SUCH_COLUMN = "There's no such column in this profile."
OUT_OF_DATE = "{} changed since this run. Profile it again to see its values."
NO_LONGER_CHOSEN = "{} is no longer chosen. Choose it again to see its values."
VALUES_OUT_OF_MEMORY = "DrivnX ran out of memory working out these values."
VALUES_FAILED = "DrivnX couldn't work out these values."
TOO_LONG = "Reading {} took longer than this run's time limit of {} minute{}."
STOPPED_AT_TIME_LIMIT = (
    "{} was stopped at this run's time limit, so it has no values to show."
)


class ProfileRunRequest(BaseModel):
    source_id: str
    mode: Mode
    # A database's write access as a refusal gave it, which the user confirmed (AC-16).
    write_access: dict | None = None
    # A database's full profile: each object's limit, in minutes (DRIVN-53).
    time_limit_minutes: int | None = Field(None, ge=1, le=120)


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
    # The object a database's running run is reading, as `schema.object` (AC-17).
    current_object: str | None = None
    # Whether a database run's role could only read its objects (AC-16); None for a file's run.
    read_only: bool | None = None
    # A database run's objects as `schema.object`, in the order it read them (AC-31); None for a
    # file's run.
    object_names: list[str] | None = None


class PatternResponse(BaseModel):
    pattern: str
    count: int
    cut: bool


class MetricResponse(BaseModel):
    # A database run's object, as `schema.object`; None for a file's run.
    object: str | None = None
    column: str | None
    metric: str
    value: int | float | list[PatternResponse]
    scope: str
    precision: str


class PatternsReadResponse(BaseModel):
    """How a full profile's fast reading read the rows its masked patterns are from."""

    method: str
    rows: int


class ObjectReadResponse(BaseModel):
    """How a database run read an object (AC-18, AC-19): `page_sample`, `whole`, `first_rows`
    (read whole, but larger than its estimate), `view_first_rows`, or `full` on a full profile."""

    object: str
    # Its schema and name apart, which its values are asked for by (DRIVN-56), as `object`
    # can't tell `"a.b".c` from `a."b.c"`.
    schema: str
    name: str
    method: str
    # A full profile stopped reading it at its time limit, so it has no results (AC-20).
    stopped_at_time_limit: bool = False
    # On a full profile, its fast reading, as its masked patterns come from it (the Assumptions).
    patterns_from: PatternsReadResponse | None = None
    # A refresh has since found it gone, or of another kind or other columns, so its results are
    # out of date (AC-32).
    out_of_date: bool = False


class ProfileRunDetail(ProfileRunResponse):
    metrics: list[MetricResponse]
    # A database run's objects, in the order it read them; none for a file's run.
    objects: list[ObjectReadResponse] = []
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
    # The part of the file or the object read, as a metric's scope says it.
    scope: str
    # A database run's object, as `schema.object`, its kind, and how its rows were read, as
    # `ObjectReadResponse.method` says it (DRIVN-56); None for a file's run.
    object: str | None = None
    kind: str | None = None
    method: str | None = None
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


def _values_response(
    run_id: str, column: str, scope: str, found: ColumnValues, **database: str
) -> ValuesResponse:
    return ValuesResponse(
        profile_run_id=run_id,
        column=column,
        scope=scope,
        **database,
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
        current_object=object_of(run.id) if run.status == "running" else None,
        read_only=None if run.write_access is None else not can_write(run.write_access),
        object_names=None if run.objects is None else list(run.objects),
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


def database_run_names(
    workspace: Workspace, scope: dict
) -> tuple[dict[str, str], dict[str, str]]:
    """A database run's objects by dataset ID, as `schema.object`, and their columns by field
    ID, from what its scope says it read."""
    objects = {found["dataset_id"]: found for found in scope["objects"]}
    named = {
        dataset_id: f"{found['schema']}.{found['name']}" for dataset_id, found in objects.items()
    }
    columns = {
        field_id: name
        for dataset_id in objects
        for field_id, name in read_field_names(workspace.engine, dataset_id).items()
    }
    return named, columns


def profile_runs_router(workspace: Workspace, kept: KeptPasswords) -> APIRouter:
    router = APIRouter(prefix="/api/profile-runs")

    def existing_run(run_id: str) -> ListedProfileRun:
        run = find_listed_profile_run(workspace.engine, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="There's no such profile run.")
        return run

    def start_database_run(
        database: StoredPostgresSource,
        mode: Mode,
        confirmed: dict | None,
        time_limit_minutes: int | None,
    ) -> str:
        """A fast or full profile of the database's chosen objects (DRIVN-51, DRIVN-53). Returns
        its ID."""
        if time_limit_minutes is not None and mode != "full":
            raise HTTPException(status_code=422, detail=TIME_LIMIT_NOT_HERE)
        # Only chosen objects are profiled (AC-11).
        objects = read_chosen_objects(workspace.engine, database.id)
        if not objects:
            raise HTTPException(status_code=409, detail=NOTHING_CHOSEN)
        # Before reading the database, so a second run opens no session there (AC-23). The start
        # checks again, for two requests at once.
        if any(run.status == "running" for run in list_profile_runs(workspace.engine)):
            raise HTTPException(status_code=409, detail=RUN_IN_PROGRESS)
        known = password_for(kept, database)
        try:
            # As the database is now, not as it was listed (AC-13).
            discovery = discover(details_of(database), known.password, workspace.temp_directory)
        except ConnectFailure as failure:
            raise read_failed(kept, database.id, known, failure) from None
        changed = changed_since_listed(workspace.engine, database.id, found_objects(discovery))
        if changed:
            raise HTTPException(
                status_code=409, detail=OBJECTS_CHANGED.format(", ".join(changed))
            )
        # Only once the user has confirmed, if the role could change what's chosen (AC-16), and
        # again whenever that changes.
        access = write_access(discovery, objects)
        if can_write(access) and access != read_write_confirmation(workspace.engine, database.id):
            if confirmed != access:
                raise refusal(access)
            save_write_confirmation(workspace.engine, database.id, access)
        # From the estimates as they are now, not as they were listed (ADR 0005).
        estimates = {(found.schema, found.name): found for found in discovery.objects}
        readings = {
            chosen.dataset.id: reading_for(
                chosen.kind, estimates[(chosen.schema, chosen.name)].estimated_rows
            )
            for chosen in objects
        }
        try:
            run_id = start_database_profile(
                workspace,
                mode,
                details_of(database),
                database,
                known.password,
                objects,
                readings,
                write_access=access,
                time_limit_minutes=time_limit_minutes,
                # So a run's read ends on the server even if DrivnX is killed (AC-33).
                check_connection=discovery.checks_closed_connections,
            )
        except RunInProgress:
            raise HTTPException(status_code=409, detail=RUN_IN_PROGRESS) from None
        except Busy:
            raise HTTPException(status_code=409, detail=BUSY) from None
        except SettingsChanged:
            raise HTTPException(status_code=409, detail=OBJECTS_CHANGED_JUST_NOW) from None
        return run_id

    @router.post("", status_code=202)
    def start_run(body: ProfileRunRequest) -> ProfileRunResponse:
        no_such_source = HTTPException(status_code=404, detail="There's no such source.")
        database = find_source(workspace.engine, body.source_id)
        if isinstance(database, StoredPostgresSource):
            return _response(
                existing_run(
                    start_database_run(
                        database, body.mode, body.write_access, body.time_limit_minutes
                    )
                )
            )
        try:
            # Before the settings, so a correction saved after either replaces this dataset, and
            # the start is refused instead of reading with settings that aren't this dataset's.
            dataset = read_dataset(workspace.engine, body.source_id)
        except NoResultFound:
            raise no_such_source from None
        source = find_source(workspace.engine, body.source_id)
        if source is None:
            raise no_such_source
        if body.time_limit_minutes is not None:
            raise HTTPException(status_code=422, detail=TIME_LIMIT_NOT_HERE)
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
        scope = read_profile_run(workspace.engine, run.id).scope
        if run.dataset_id is None:
            return database_run_detail(run, scope, observations)
        names = read_field_names(workspace.engine, run.dataset_id)
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

    def database_run_detail(
        run: ListedProfileRun, scope: dict, observations: list[Observation]
    ) -> ProfileRunDetail:
        """Each metric with its object, and how each object was read (AC-24)."""
        objects = {found["dataset_id"]: found for found in scope["objects"]}
        named, columns = database_run_names(workspace, scope)
        # A whole read that came back larger than its estimate was measured on its first rows.
        sampled = {
            seen.dataset_id
            for seen in observations
            if seen.metric == "rows_read" and seen.scope == "sample"
        }
        stopped = {
            seen.dataset_id for seen in observations if seen.metric == TIME_LIMIT_REACHED
        }
        fast_readings = {
            seen.dataset_id: seen for seen in observations if seen.metric == SAMPLE_ROWS_READ
        }
        replaced = replaced_datasets(workspace.engine, list(objects))

        def patterns_from(dataset_id: str, planned: str) -> PatternsReadResponse | None:
            seen = fast_readings.get(dataset_id)
            if seen is None:
                return None
            # As for a fast run: read whole, but larger than its estimate, so its first rows.
            method = "first_rows" if planned == "whole" and seen.scope == "sample" else planned
            return PatternsReadResponse(method=method, rows=seen.value)

        def read_as(dataset_id: str, planned: str) -> str:
            if run.mode == "full":
                # Every row, whatever its fast reading (DRIVN-53).
                return "full"
            if planned == "whole" and dataset_id in sampled:
                return "first_rows"
            return planned
        return ProfileRunDetail(
            **_response(run).model_dump(),
            file_changed=None,
            objects=[
                ObjectReadResponse(
                    object=named[dataset_id],
                    schema=found["schema"],
                    name=found["name"],
                    method=read_as(dataset_id, found["method"]),
                    stopped_at_time_limit=dataset_id in stopped,
                    patterns_from=patterns_from(dataset_id, found["method"]),
                    out_of_date=dataset_id in replaced,
                )
                for dataset_id, found in objects.items()
            ],
            metrics=[
                MetricResponse(
                    object=named[seen.dataset_id],
                    column=columns[seen.field_id] if seen.field_id else None,
                    metric=seen.metric,
                    value=seen.value,
                    scope=seen.scope,
                    precision=seen.precision,
                )
                for seen in observations
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

    def database_values(
        run: ListedProfileRun,
        schema: str | None,
        name: str | None,
        column: str,
        confirmed: bool,
    ) -> ValuesResponse:
        """A database column's values, read again as the run read its object (DRIVN-56)."""
        saved = read_profile_run(workspace.engine, run.id)
        # By schema and name, as `schema.object` can't tell `"a.b".c` from `a."b.c"`.
        planned = next(
            (
                found
                for found in saved.scope["objects"]
                if (found["schema"], found["name"]) == (schema, name)
            ),
            None,
        )
        if planned is None:
            raise HTTPException(status_code=404, detail=NO_SUCH_OBJECT)
        shown = f"{schema}.{name}"
        # Its rows can't be read again as the run read them (decision B, AC-32).
        if replaced_datasets(workspace.engine, [planned["dataset_id"]]):
            raise HTTPException(status_code=409, detail=OUT_OF_DATE.format(shown))
        chosen = next(
            (
                found
                for found in read_chosen_objects(workspace.engine, run.source_id)
                if found.dataset.id == planned["dataset_id"]
            ),
            None,
        )
        # Only chosen objects are read (AC-11).
        if chosen is None:
            raise HTTPException(status_code=409, detail=NO_LONGER_CHOSEN.format(shown))
        # No results, so no rows to read again (AC-20).
        if any(
            seen.dataset_id == chosen.dataset.id and seen.metric == TIME_LIMIT_REACHED
            for seen in read_observations(workspace.engine, run.id)
        ):
            raise HTTPException(status_code=409, detail=STOPPED_AT_TIME_LIMIT.format(shown))
        position = next(
            (at for at, field in enumerate(chosen.dataset.fields) if field.name == column), None
        )
        if position is None:
            raise HTTPException(status_code=404, detail=NO_SUCH_COLUMN)
        # Before the database is read, so a refusal shows nothing (AC-27).
        if not confirmed and flagged(run.id, chosen.dataset.fields[position].id):
            raise HTTPException(status_code=409, detail=POSSIBLY_SENSITIVE)
        database = find_source(workspace.engine, run.source_id)
        assert isinstance(database, StoredPostgresSource)
        known = password_for(kept, database)
        try:
            method, found = object_values_of(
                workspace,
                details_of(database),
                known.password,
                chosen,
                position,
                run.mode,
                Reading(planned["method"], planned["percent"]),
                # A database's run always saves one.
                saved.seed,
                # A full run saves its limit; a fast one has none.
                time_limit_minutes=saved.scope.get(
                    "time_limit_minutes", DEFAULT_TIME_LIMIT_MINUTES
                ),
                # Saved by runs since DRIVN-56; an earlier one's values go without it.
                check_connection=saved.scope.get("check_connection", False),
            )
        except Busy:
            raise HTTPException(status_code=409, detail=BUSY) from None
        except ConnectFailure as failure:
            # The saved metrics still show (AC-28): only this request failed.
            raise read_failed(kept, database.id, known, failure) from None
        except TimeLimitReached as reached:
            minutes = reached.minutes
            raise HTTPException(
                status_code=422,
                detail=TOO_LONG.format(shown, minutes, "" if minutes == 1 else "s"),
            ) from None
        # Never DuckDB's text, which can quote a value it read (AC-29).
        except duckdb.OutOfMemoryException:
            raise HTTPException(status_code=422, detail=VALUES_OUT_OF_MEMORY) from None
        except duckdb.Error:
            raise HTTPException(status_code=422, detail=VALUES_FAILED) from None
        return _values_response(
            run.id,
            column,
            read_scope(method),
            found,
            object=shown,
            kind=planned["kind"],
            method=method,
        )

    @router.get("/{run_id}/values")
    def get_values(
        run_id: str,
        column: str,
        confirmed: bool = False,
        schema: str | None = None,
        object: str | None = None,
    ) -> ValuesResponse:
        """A column's values from the part of the file, or the object, the run read (AC-30,
        DRIVN-45 AC-28), computed now."""
        run = existing_run(run_id)
        if run.status != "completed":
            raise HTTPException(status_code=409, detail=NOT_COMPLETED)
        if run.dataset_id is None:
            return database_values(run, schema, object, column, confirmed)
        # Its columns may have been renamed since, and its settings no longer read the file.
        if not run.current_settings:
            raise HTTPException(status_code=409, detail=EARLIER_SETTINGS)
        dataset = read_dataset(workspace.engine, run.source_id)
        position = next(
            (at for at, field in enumerate(dataset.fields) if field.name == column), None
        )
        if position is None:
            raise HTTPException(status_code=404, detail=NO_SUCH_COLUMN)
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
        return _values_response(
            run.id, column, "full" if found.whole_file else "sample", found
        )

    return router
