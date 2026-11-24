"""[DEBUG-7c1e] Throwaway probe: what a full run with a short time limit ends as, and why."""

import pytest
from artifacts.profiles import read_observations
from artifacts.sources import read_chosen_objects
from artifacts.workspace import Workspace
from local_api import profile_jobs

from .test_postgres_findings import chosen_source, objects  # noqa: F401 (the fixture)
from .test_postgres_password_store import (  # noqa: F401 (the fixtures)
    StartApp,
    start_app,
    workspace,
)
from .test_postgres_profile_api import ended

LIMITS = [float(s) for s in __import__("os").environ.get("PROBE_LIMITS", "0.25").split(",")]
ATTEMPTS = int(__import__("os").environ.get("PROBE_ATTEMPTS", "3"))


@pytest.mark.parametrize("seconds", LIMITS)
@pytest.mark.parametrize("attempt", range(ATTEMPTS))
def test_probe(
    start_app: StartApp,
    workspace: Workspace,
    postgres_server: str,
    monkeypatch: pytest.MonkeyPatch,
    seconds: float,
    attempt: int,
) -> None:
    monkeypatch.setattr(profile_jobs, "SECONDS_PER_MINUTE", seconds)
    client = start_app(None)
    source_id = chosen_source(client, "gaps", "late_gaps")
    response = client.post(
        "/api/profile-runs",
        json={"source_id": source_id, "mode": "full", "time_limit_minutes": 1},
    )
    run_id = response.json()["id"]
    run = ended(client, run_id)
    names = {c.dataset.id: c.name for c in read_chosen_objects(workspace.engine, source_id)}
    stopped = sorted(
        names[seen.dataset_id]
        for seen in read_observations(workspace.engine, run_id)
        if seen.metric == profile_jobs.TIME_LIMIT_REACHED
    )
    print(
        f"[DEBUG-7c1e] RESULT limit={seconds}s status={run['status']} stopped={stopped}"
        f" failure={run['failure']!r}"
    )
