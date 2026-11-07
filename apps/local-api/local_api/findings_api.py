"""`/api/findings`: a completed run's findings, each with the evidence it cites."""

from artifacts.profiles import find_listed_profile_run, read_findings, read_observations
from artifacts.sources import read_field_names
from artifacts.workspace import Workspace
from domain.trust import TrustState
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from local_api.profile_runs_api import MetricResponse


class FindingResponse(BaseModel):
    id: str
    profile_run_id: str
    kind: str
    interpretation: str
    why_it_matters: str
    recommended_action: str
    state: TrustState
    evidence: list[MetricResponse]


def findings_router(workspace: Workspace) -> APIRouter:
    router = APIRouter(prefix="/api/findings")

    @router.get("")
    def get_findings(profile_run_id: str) -> list[FindingResponse]:
        run = find_listed_profile_run(workspace.engine, profile_run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="There's no such profile run.")
        observations = {seen.id: seen for seen in read_observations(workspace.engine, run.id)}
        names = read_field_names(workspace.engine, run.dataset_id)
        return [
            FindingResponse(
                id=finding.id,
                profile_run_id=finding.profile_run_id,
                kind=finding.kind,
                interpretation=finding.interpretation,
                why_it_matters=finding.why_it_matters,
                recommended_action=finding.recommended_action,
                # Nothing reviews a finding further (first slice), so it's always Inferred.
                state=TrustState.INFERRED,
                evidence=[
                    MetricResponse(
                        column=names.get(observations[cited].field_id)
                        if observations[cited].field_id
                        else None,
                        metric=observations[cited].metric,
                        value=observations[cited].value,
                        scope=observations[cited].scope,
                        precision=observations[cited].precision,
                    )
                    for cited in finding.observation_ids
                ],
            )
            for finding in read_findings(workspace.engine, run.id)
        ]

    return router
