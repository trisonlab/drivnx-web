"""`/api/profile-runs`: starting a profile run, and reading its status and results."""

from typing import Literal

from artifacts.profiles import (
    ListedProfileRun,
    RunInProgress,
    find_listed_profile_run,
    list_profile_runs,
    read_observations,
)
from artifacts.sources import find_source, read_field_names
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from local_api.profile_jobs import start_fast_profile
from local_api.sources_api import (
    cannot_read,
    current_encoding,
    is_available,
    modified_at,
)

RUN_IN_PROGRESS = "A profile is already running. Wait for it to finish, then try again."


class ProfileRunRequest(BaseModel):
    source_id: str
    mode: Literal["fast"]


class ProfileRunResponse(BaseModel):
    id: str
    source_id: str
    mode: str
    status: str
    started_at: str
    ended_at: str | None
    current_settings: bool


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
        source = find_source(workspace.engine, body.source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="There's no such source.")
        if not is_available(source.path):
            raise cannot_read(
                "Conflux can't find this file. It may have moved or been deleted."
            )
        try:
            encoding = current_encoding(source)
            stat = source.path.stat()
        except CsvError as error:
            raise cannot_read(str(error)) from None
        except PermissionError:
            raise cannot_read(
                "Conflux doesn't have permission to read this file."
            ) from None
        except OSError:
            raise cannot_read("Conflux couldn't read this file.") from None
        try:
            run_id = start_fast_profile(
                workspace, source, encoding, stat, modified_at(stat)
            )
        except RunInProgress:
            raise HTTPException(status_code=409, detail=RUN_IN_PROGRESS) from None
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
