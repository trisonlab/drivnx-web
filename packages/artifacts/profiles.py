"""Profile runs, their observations and findings."""

import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from domain.trust import Action, Actor, ItemKind, next_state
from sqlalchemy import Connection, Engine, Row, text
from sqlalchemy.exc import DBAPIError

from artifacts.provenance import (
    NO_DETAILS,
    EventDetails,
    now,
    record_event,
    record_events,
)
from artifacts.schema import WorkspaceError
from artifacts.store import write_transaction

# Holds the write lock for 0.2 to 0.5 s. At 40,000, a review waiting behind it got WorkspaceBusy.
MAX_OBSERVATIONS_PER_TRANSACTION = 1000
# `interrupted` isn't here: only startup can know that nothing is still writing a run.
_ENDINGS = ("completed", "cancelled", "failed")
_DID_NOT_COMPLETE = ("cancelled", "failed", "interrupted")


@dataclass(frozen=True)
class ProfileRun:
    id: str
    scope: dict[str, Any]
    mode: str
    sample_size: int | None
    strategy: str | None
    seed: int | None
    status: str
    started_at: str
    ended_at: str | None


@dataclass(frozen=True)
class NewObservation:
    dataset_id: str
    field_id: str | None
    metric: str
    value: Any
    scope: str
    method: str
    precision: str


@dataclass(frozen=True)
class Observation:
    id: str
    profile_run_id: str
    dataset_id: str
    field_id: str | None
    metric: str
    value: Any
    scope: str
    method: str
    precision: str


@dataclass(frozen=True)
class NewFinding:
    profile_run_id: str
    observation_ids: tuple[str, ...]
    interpretation: str
    why_it_matters: str
    recommended_action: str
    kind: str


@dataclass(frozen=True)
class Finding:
    id: str
    profile_run_id: str
    observation_ids: tuple[str, ...]
    interpretation: str
    why_it_matters: str
    recommended_action: str
    kind: str


def _insert_run(
    connection: Connection,
    scope: dict[str, Any],
    mode: str,
    sample_size: int | None,
    strategy: str | None,
    seed: int | None,
) -> str:
    run_id = uuid.uuid4().hex
    connection.execute(
        text(
            """
            INSERT INTO profile_run
                (id, scope, mode, sample_size, strategy, seed, status, started_at)
            VALUES
                (:id, :scope, :mode, :sample_size, :strategy, :seed, 'running', :started_at)
            """
        ),
        {
            "id": run_id,
            "scope": json.dumps(scope),
            "mode": mode,
            "sample_size": sample_size,
            "strategy": strategy,
            "seed": seed,
            "started_at": now(),
        },
    )
    return run_id


def start_profile_run(
    engine: Engine,
    scope: dict[str, Any],
    mode: str,
    sample_size: int | None = None,
    strategy: str | None = None,
    seed: int | None = None,
) -> str:
    """In its own transaction, because a run can take minutes (ADR 0002 point 3)."""
    with write_transaction(engine) as connection:
        return _insert_run(connection, scope, mode, sample_size, strategy, seed)


class RunInProgress(Exception):
    pass


class SettingsChanged(Exception):
    """The settings a run was about to read with have just been replaced."""


def start_only_profile_run(
    engine: Engine,
    scope: dict[str, Any],
    mode: str,
    sample_size: int | None = None,
    strategy: str | None = None,
) -> str:
    """Start a run unless one is in progress, or its dataset was replaced, `SettingsChanged`.

    Both are checked under the write lock, so two starts can't both pass, and a correction can't
    be saved between the check and the start.
    """
    with write_transaction(engine) as connection:
        running = connection.execute(
            text("SELECT 1 FROM profile_run WHERE status = 'running' LIMIT 1")
        ).first()
        if running:
            raise RunInProgress
        replaced = connection.execute(
            text("SELECT replaced_at IS NOT NULL FROM dataset WHERE id = :id"),
            {"id": scope["dataset_id"]},
        ).scalar()
        if replaced:
            raise SettingsChanged
        return _insert_run(connection, scope, mode, sample_size, strategy, None)


def read_profile_run(engine: Engine, run_id: str) -> ProfileRun:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT id, scope, mode, sample_size, strategy, seed, status, started_at, ended_at
                FROM profile_run WHERE id = :id
                """
            ),
            {"id": run_id},
        ).one()
    return ProfileRun(
        row.id,
        json.loads(row.scope),
        row.mode,
        row.sample_size,
        row.strategy,
        row.seed,
        row.status,
        row.started_at,
        row.ended_at,
    )


@dataclass(frozen=True)
class ListedProfileRun:
    id: str
    source_id: str
    dataset_id: str
    mode: str
    status: str
    started_at: str
    ended_at: str | None
    current_settings: bool


_SELECT_LISTED_RUNS = """
    SELECT profile_run.id, dataset.source_id, dataset.id AS dataset_id, profile_run.mode,
           profile_run.status, profile_run.started_at, profile_run.ended_at,
           dataset.replaced_at IS NULL AS current_settings
    FROM profile_run
    JOIN dataset ON dataset.id = json_extract(profile_run.scope, '$.dataset_id')
"""


def _listed(row: Row) -> ListedProfileRun:
    return ListedProfileRun(
        row.id,
        row.source_id,
        row.dataset_id,
        row.mode,
        row.status,
        row.started_at,
        row.ended_at,
        bool(row.current_settings),
    )


def list_profile_runs(engine: Engine) -> list[ListedProfileRun]:
    """Newest first."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(f"{_SELECT_LISTED_RUNS} ORDER BY profile_run.rowid DESC")
        ).all()
    return [_listed(row) for row in rows]


def find_listed_profile_run(engine: Engine, run_id: str) -> ListedProfileRun | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(f"{_SELECT_LISTED_RUNS} WHERE profile_run.id = :id"), {"id": run_id}
        ).first()
    return _listed(row) if row else None


def _status_of(connection: Connection, run_id: str) -> str | None:
    return connection.execute(
        text("SELECT status FROM profile_run WHERE id = :id"), {"id": run_id}
    ).scalar()


def finish_profile_run(
    engine: Engine, run_id: str, status: str, findings: Iterable[NewFinding] = ()
) -> None:
    """In its own transaction, after the run's observations (ADR 0002 point 3).

    A completed run's findings are saved in the same transaction, so a run that ends any other way
    has none, and a completed run never has only some of them (AC-26).
    """
    if status not in _ENDINGS:
        raise ValueError(f"A profile run can't end as {status}.")
    findings = list(findings)
    if findings and status != "completed":
        raise ValueError("Only a completed run takes findings.")
    with write_transaction(engine) as connection:
        current = _status_of(connection, run_id)
        if current is None:
            raise ValueError("There's no such profile run.")
        if current != "running":
            raise ValueError("This profile run has already ended.")
        if any(finding.profile_run_id != run_id for finding in findings):
            raise ValueError("A finding must be from the run it ends with.")
        _insert_findings(connection, Actor.SYSTEM, findings, NO_DETAILS)
        connection.execute(
            text(
                """
                UPDATE profile_run SET status = :status, ended_at = :ended_at
                WHERE id = :id
                """
            ),
            {"id": run_id, "status": status, "ended_at": now()},
        )


def mark_interrupted_runs(engine: Engine) -> None:
    """For startup only: the ADR 0001 lock means no process can still be writing these runs."""
    try:
        with write_transaction(engine) as connection:
            connection.execute(
                text(
                    """
                    UPDATE profile_run SET status = 'interrupted', ended_at = :ended_at
                    WHERE status = 'running'
                    """
                ),
                {"ended_at": now()},
            )
    except DBAPIError as error:
        raise WorkspaceError(
            f"Can't finish opening this workspace: {error.orig}"
        ) from None


def add_observations(
    engine: Engine,
    actor: Actor,
    run_id: str,
    observations: Iterable[NewObservation],
    details: EventDetails = NO_DETAILS,
) -> None:
    """One short transaction, so review actions aren't kept waiting (ADR 0002 point 3).

    `observations` is read before taking the write lock, so slow work there can't hold it.
    """
    state = next_state(actor, Action.RECORD_OBSERVED, ItemKind.OBSERVATION, None)
    batch = list(observations)
    if len(batch) > MAX_OBSERVATIONS_PER_TRANSACTION:
        raise ValueError(
            f"One transaction holds at most {MAX_OBSERVATIONS_PER_TRANSACTION} observations."
        )
    with write_transaction(engine) as connection:
        if _status_of(connection, run_id) != "running":
            raise ValueError("This profile run isn't running.")
        for observation in batch:
            observation_id = uuid.uuid4().hex
            connection.execute(
                text(
                    """
                    INSERT INTO observation
                        (id, profile_run_id, dataset_id, field_id, metric, value, scope, method,
                         precision)
                    VALUES
                        (:id, :run, :dataset_id, :field_id, :metric, :value, :scope, :method,
                         :precision)
                    """
                ),
                {
                    "id": observation_id,
                    "run": run_id,
                    "dataset_id": observation.dataset_id,
                    "field_id": observation.field_id,
                    "metric": observation.metric,
                    "value": json.dumps(observation.value),
                    "scope": observation.scope,
                    "method": observation.method,
                    "precision": observation.precision,
                },
            )
            record_event(
                connection,
                actor,
                Action.RECORD_OBSERVED,
                None,
                state,
                "observation_id",
                observation_id,
                details,
            )


def read_observations(engine: Engine, run_id: str) -> list[Observation]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, profile_run_id, dataset_id, field_id, metric, value, scope, method,
                       precision
                FROM observation WHERE profile_run_id = :run ORDER BY rowid
                """
            ),
            {"run": run_id},
        ).all()
    return [
        Observation(
            row.id,
            row.profile_run_id,
            row.dataset_id,
            row.field_id,
            row.metric,
            json.loads(row.value),
            row.scope,
            row.method,
            row.precision,
        )
        for row in rows
    ]


def add_finding(
    engine: Engine,
    actor: Actor,
    finding: NewFinding,
    details: EventDetails = NO_DETAILS,
) -> str:
    """A finding's interpretation is Inferred (Section 5), and nothing reviews it further."""
    with write_transaction(engine) as connection:
        # `read_findings` would never return it, so the write would seem to work.
        if _status_of(connection, finding.profile_run_id) in _DID_NOT_COMPLETE:
            raise ValueError("A run that didn't complete takes no findings.")
        return _insert_findings(connection, actor, [finding], details)[0]


def _insert_findings(
    connection: Connection,
    actor: Actor,
    findings: Sequence[NewFinding],
    details: EventDetails,
) -> list[str]:
    """In a few statements, not a few per finding, as a wide file's findings all go into one
    transaction that holds the write lock (ADR 0002 point 3)."""
    state = next_state(actor, Action.CREATE_INFERRED, ItemKind.FINDING, None)
    runs = sorted({finding.profile_run_id for finding in findings})
    owners = dict(
        connection.execute(
            text(
                "SELECT id, profile_run_id FROM observation "
                "WHERE profile_run_id IN (SELECT value FROM json_each(:runs))"
            ),
            {"runs": json.dumps(runs)},
        ).all()
    )
    for finding in findings:
        if not finding.observation_ids:
            raise ValueError("A finding needs at least one observation.")
        if len(set(finding.observation_ids)) != len(finding.observation_ids):
            raise ValueError("A finding can't list an observation twice.")
        if any(owners.get(cited) != finding.profile_run_id for cited in finding.observation_ids):
            raise ValueError("A finding's observations must be from its own run.")
    finding_ids = [uuid.uuid4().hex for _ in findings]
    if not findings:
        return finding_ids
    connection.execute(
        text(
            """
            INSERT INTO finding
                (id, profile_run_id, kind, interpretation, why_it_matters,
                 recommended_action)
            VALUES
                (:id, :run, :kind, :interpretation, :why_it_matters, :recommended_action)
            """
        ),
        [
            {
                "id": finding_id,
                "run": finding.profile_run_id,
                "kind": finding.kind,
                "interpretation": finding.interpretation,
                "why_it_matters": finding.why_it_matters,
                "recommended_action": finding.recommended_action,
            }
            for finding_id, finding in zip(finding_ids, findings, strict=True)
        ],
    )
    connection.execute(
        text(
            """
            INSERT INTO finding_observation (finding_id, observation_id, position)
            VALUES (:finding, :observation, :position)
            """
        ),
        [
            {"finding": finding_id, "observation": cited, "position": position}
            for finding_id, finding in zip(finding_ids, findings, strict=True)
            for position, cited in enumerate(finding.observation_ids)
        ],
    )
    record_events(
        connection,
        actor,
        Action.CREATE_INFERRED,
        None,
        state,
        "finding_id",
        finding_ids,
        details,
    )
    return finding_ids


def _with_observations(connection: Connection, row: Row) -> Finding:
    observations = connection.execute(
        text(
            """
            SELECT observation_id FROM finding_observation
            WHERE finding_id = :id ORDER BY position
            """
        ),
        {"id": row.id},
    ).scalars()
    return Finding(
        row.id,
        row.profile_run_id,
        tuple(observations),
        row.interpretation,
        row.why_it_matters,
        row.recommended_action,
        row.kind,
    )


def read_finding(engine: Engine, finding_id: str) -> Finding:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT id, profile_run_id, interpretation, why_it_matters, recommended_action,
                       kind
                FROM finding WHERE id = :id
                """
            ),
            {"id": finding_id},
        ).one()
        return _with_observations(connection, row)


def read_findings(engine: Engine, run_id: str | None = None) -> list[Finding]:
    """Findings from completed runs only, so a partial run is never shown as complete.

    In the order they were saved, which is the fixed order by kind. `run_id` keeps one run's.
    """
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT finding.id, finding.profile_run_id, finding.interpretation,
                       finding.why_it_matters, finding.recommended_action, finding.kind
                FROM finding JOIN profile_run ON profile_run.id = finding.profile_run_id
                WHERE profile_run.status = 'completed'
                  AND (:run IS NULL OR finding.profile_run_id = :run)
                ORDER BY finding.rowid
                """
            ),
            {"run": run_id},
        ).all()
        return [_with_observations(connection, row) for row in rows]
