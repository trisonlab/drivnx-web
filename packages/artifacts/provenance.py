"""Writing ProvenanceEvents. They are only ever inserted (ADR 0002 point 4)."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from domain.trust import Action, Actor, Rejected, TrustState
from sqlalchemy import Connection, text


@dataclass(frozen=True)
class EventDetails:
    """What the rest of a ProvenanceEvent records (Section 12)."""

    ai_provider: str | None = None
    ai_model: str | None = None
    prompt_version: str | None = None
    approved_context_categories: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    reviewer: str | None = None


NO_DETAILS = EventDetails()


def now() -> str:
    return datetime.now(UTC).isoformat()


def record_event(
    connection: Connection,
    actor: Actor,
    action: Action,
    from_state: TrustState | None,
    to_state: TrustState,
    target_column: str,
    target_id: str,
    details: EventDetails,
    previous_value: object = None,
) -> None:
    """`target_column` is a column name written in this module's callers, never input."""
    if actor is Actor.AI and not (
        details.ai_provider and details.ai_model and details.prompt_version
    ):
        raise Rejected(
            "An AI event must say which provider, model and prompt wrote it."
        )
    connection.execute(
        text(
            f"""
            INSERT INTO provenance_event (
                id, author, occurred_at, action, from_state, to_state, previous_value,
                ai_provider, ai_model, prompt_version, approved_context_categories,
                evidence_references, reviewer, {target_column}
            ) VALUES (
                :id, :author, :occurred_at, :action, :from_state, :to_state, :previous_value,
                :ai_provider, :ai_model, :prompt_version, :approved_context_categories,
                :evidence_references, :reviewer, :target_id
            )
            """
        ),
        {
            "id": uuid.uuid4().hex,
            "author": actor.value,
            "occurred_at": now(),
            "action": action.value,
            "from_state": from_state.value if from_state else None,
            "to_state": to_state.value,
            "previous_value": None
            if previous_value is None
            else json.dumps(previous_value),
            "ai_provider": details.ai_provider,
            "ai_model": details.ai_model,
            "prompt_version": details.prompt_version,
            "approved_context_categories": json.dumps(
                details.approved_context_categories
            ),
            "evidence_references": json.dumps(details.evidence_references),
            "reviewer": details.reviewer,
            "target_id": target_id,
        },
    )
