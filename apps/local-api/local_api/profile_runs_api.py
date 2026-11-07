"""`/api/profile-runs`: starting a profile run, and reading its status and results."""

from artifacts.profiles import (
    ListedProfileRun,
    RunInProgress,
    SettingsChanged,
    find_listed_profile_run,
    list_profile_runs,
    read_observations,
)
from artifacts.sources import StoredSource, find_source, read_dataset, read_field_names
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError, read_page, sniffed_columns
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import NoResultFound

from local_api.profile_jobs import Mode, cancel_profile, failure_of, start_profile
from local_api.sources_api import (
    cannot_read,
    current_encoding,
    is_available,
    modified_at,
)

RUN_IN_PROGRESS = "A profile is already running. Wait for it to finish, then try again."
ALREADY_FINISHED = "This profile has already finished."
SETTINGS_CHANGED = "This file's settings changed just now. Start the profile again."
COLUMNS_NO_LONGER_MATCH = (
    "This file's columns no longer match the saved ones. "
    "Open this source's settings and save them to read the columns again."
)


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
    # Why it failed, when Conflux knows. Not kept across a restart.
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


def profile_runs_router(workspace: Workspace) -> APIRouter:
    router = APIRouter(prefix="/api/profile-runs")

    def columns_now(source: StoredSource, encoding: str) -> list[str] | None:
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

    def existing_run(run_id: str) -> ListedProfileRun:
        run = find_listed_profile_run(workspace.engine, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="There's no such profile run.")
        return run

    @router.post("", status_code=202)
    def start_run(body: ProfileRunRequest) -> ProfileRunResponse:
        no_such_source = HTTPException(status_code=404, detail="There's no such source.")
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
            raise cannot_read(
                "Conflux can't find this file. It may have moved or been deleted."
            )
        try:
            encoding = current_encoding(source)
            stat = source.path.stat()
            columns = columns_now(source, encoding)
        except CsvError as error:
            raise cannot_read(str(error)) from None
        except PermissionError:
            raise cannot_read(
                "Conflux doesn't have permission to read this file."
            ) from None
        except OSError:
            raise cannot_read("Conflux couldn't read this file.") from None
        if columns is not None and columns != [field.name for field in source.fields]:
            raise cannot_read(COLUMNS_NO_LONGER_MATCH)
        try:
            run_id = start_profile(
                workspace, body.mode, source, dataset, encoding, stat, modified_at(stat)
            )
        except RunInProgress:
            raise HTTPException(status_code=409, detail=RUN_IN_PROGRESS) from None
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
        return ProfileRunDetail(
            **_response(run).model_dump(),
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

    return router
