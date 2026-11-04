"""Contracts, dictionary entries and contract rules, and the reviewing of them."""

import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from domain.trust import Action, Actor, ItemKind, Rejected, TrustState, next_state
from sqlalchemy import Connection, Engine, text

from artifacts.provenance import NO_DETAILS, EventDetails, record_event
from artifacts.store import write_transaction


@dataclass(frozen=True)
class Contract:
    id: str
    dataset_id: str
    version: int
    purpose: str
    limitations: str


def add_contract(
    engine: Engine, dataset_id: str, purpose: str, limitations: str
) -> str:
    """One contract covers one dataset (ADR 0003 point 3)."""
    contract_id = uuid.uuid4().hex
    with write_transaction(engine) as connection:
        existing = connection.execute(
            text("SELECT id FROM contract WHERE dataset_id = :dataset"),
            {"dataset": dataset_id},
        ).first()
        if existing is not None:
            raise ValueError("This dataset already has a contract.")
        connection.execute(
            text(
                "INSERT INTO contract (id, dataset_id, current_version) VALUES (:id, :dataset, 1)"
            ),
            {"id": contract_id, "dataset": dataset_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO contract_revision (contract_id, version, purpose, limitations)
                VALUES (:id, 1, :purpose, :limitations)
                """
            ),
            {"id": contract_id, "purpose": purpose, "limitations": limitations},
        )
    return contract_id


def read_contract(engine: Engine, contract_id: str) -> Contract:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT contract.id, contract.dataset_id, contract.current_version,
                       contract_revision.purpose, contract_revision.limitations
                FROM contract JOIN contract_revision
                    ON contract_revision.contract_id = contract.id
                    AND contract_revision.version = contract.current_version
                WHERE contract.id = :id
                """
            ),
            {"id": contract_id},
        ).one()
    return Contract(
        row.id, row.dataset_id, row.current_version, row.purpose, row.limitations
    )


@dataclass(frozen=True)
class ItemRef:
    kind: ItemKind
    id: str


@dataclass(frozen=True)
class EntryContent:
    description: str
    business_definition: str
    examples: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    limitations: str = ""
    assumptions: str = ""


@dataclass(frozen=True)
class EntrySubject:
    dataset_id: str
    field_id: str | None


@dataclass(frozen=True)
class RuleSubject:
    contract_id: str
    rule_type: str
    fields: tuple[tuple[str, str], ...]


# A rule's content is its `params`, whose shape depends on its type (ADR 0003 point 2).
Content = EntryContent | dict[str, Any]


@dataclass(frozen=True)
class Item:
    ref: ItemRef
    state: TrustState
    revision: int
    content: Content
    # Who or what the item is about, which never changes. Only `read_item` fills it in.
    subject: EntrySubject | RuleSubject | None = None


@dataclass(frozen=True)
class Event:
    author: Actor
    occurred_at: str
    action: Action
    from_state: TrustState | None
    to_state: TrustState
    previous_value: Any
    ai_provider: str | None
    ai_model: str | None
    prompt_version: str | None
    approved_context_categories: tuple[str, ...]
    evidence_references: tuple[str, ...]
    reviewer: str | None
    revision: int | None


class ItemNotFound(Exception):
    pass


@dataclass(frozen=True)
class _Tables:
    """Where each kind of item keeps its revisions. These are names in this module, never input."""

    revisions: str
    owner: str
    content: str
    event_column: str


_TABLES = {
    ItemKind.DICTIONARY_ENTRY: _Tables(
        "dictionary_entry_revision",
        "entry_id",
        "content",
        "dictionary_entry_revision_id",
    ),
    ItemKind.CONTRACT_RULE: _Tables(
        "contract_rule_revision", "rule_id", "params", "contract_rule_revision_id"
    ),
}
# These are written once and have no revisions, so an event points straight at them.
_WRITTEN_ONCE = {
    ItemKind.OBSERVATION: "observation_id",
    ItemKind.FINDING: "finding_id",
}
_CREATING = {
    TrustState.INFERRED: Action.CREATE_INFERRED,
    TrustState.CONFIRMED: Action.CREATE_CONFIRMED,
}
_LEFT_THE_INBOX = (Action.REJECT.value, Action.DEFER.value)


def _encode(kind: ItemKind, content: Content) -> str:
    """Refuse the wrong kind here: once stored, `_decode` could never read it back."""
    if kind is ItemKind.DICTIONARY_ENTRY and isinstance(content, EntryContent):
        return json.dumps(asdict(content))
    if kind is ItemKind.CONTRACT_RULE and isinstance(content, dict):
        return json.dumps(content)
    raise TypeError(f"A {kind} can't hold {type(content).__name__} content.")


def _decode(kind: ItemKind, stored: str) -> Content:
    content = json.loads(stored)
    if kind is not ItemKind.DICTIONARY_ENTRY:
        return content
    return EntryContent(
        description=content["description"],
        business_definition=content["business_definition"],
        examples=tuple(content["examples"]),
        relationships=tuple(content["relationships"]),
        limitations=content["limitations"],
        assumptions=content["assumptions"],
    )


def _action_creating(state: TrustState, actor: Actor, kind: ItemKind) -> Action:
    action = _CREATING.get(state)
    if action is None:
        raise Rejected(f"{actor} can't create a {kind} as {state}")
    return action


def _insert_revision(
    connection: Connection, ref: ItemRef, revision: int, state: TrustState, content: str
) -> str:
    tables = _TABLES[ref.kind]
    revision_id = uuid.uuid4().hex
    connection.execute(
        text(
            f"""
            INSERT INTO {tables.revisions} (id, {tables.owner}, revision, state, {tables.content})
            VALUES (:id, :owner, :revision, :state, :content)
            """
        ),
        {
            "id": revision_id,
            "owner": ref.id,
            "revision": revision,
            "state": state.value,
            "content": content,
        },
    )
    return revision_id


def _check_field_has_no_confirmed_entry(connection: Connection, field_id: str) -> None:
    """CFX-APP-first-slice/AC-55. Call this holding the write lock, so two can't both pass."""
    confirmed = connection.execute(
        text(
            """
            SELECT 1
            FROM dictionary_entry
            JOIN dictionary_entry_revision AS latest ON latest.entry_id = dictionary_entry.id
            WHERE dictionary_entry.field_id = :field
            AND latest.state = 'confirmed'
            AND latest.revision = (
                SELECT max(revision) FROM dictionary_entry_revision
                WHERE entry_id = dictionary_entry.id
            )
            LIMIT 1
            """
        ),
        {"field": field_id},
    ).first()
    if confirmed is not None:
        raise Rejected("This field already has a Confirmed dictionary entry.")


def create_entry(
    engine: Engine,
    actor: Actor,
    dataset_id: str,
    content: EntryContent,
    state: TrustState,
    field_id: str | None = None,
    details: EventDetails = NO_DETAILS,
) -> ItemRef:
    kind = ItemKind.DICTIONARY_ENTRY
    action = _action_creating(state, actor, kind)
    created = next_state(actor, action, kind, None)
    ref = ItemRef(kind, uuid.uuid4().hex)
    with write_transaction(engine) as connection:
        if created is TrustState.CONFIRMED and field_id is not None:
            _check_field_has_no_confirmed_entry(connection, field_id)
        connection.execute(
            text(
                """
                INSERT INTO dictionary_entry (id, dataset_id, field_id)
                VALUES (:id, :dataset, :field)
                """
            ),
            {"id": ref.id, "dataset": dataset_id, "field": field_id},
        )
        revision_id = _insert_revision(
            connection, ref, 1, created, _encode(kind, content)
        )
        record_event(
            connection,
            actor,
            action,
            None,
            created,
            _TABLES[kind].event_column,
            revision_id,
            details,
        )
    return ref


def create_rule(
    engine: Engine,
    actor: Actor,
    contract_id: str,
    rule_type: str,
    fields: Sequence[tuple[str, str]],
    params: dict[str, Any],
    state: TrustState,
    details: EventDetails = NO_DETAILS,
) -> ItemRef:
    """`fields` are (role, field id) pairs, and they never change (ADR 0003 point 2)."""
    kind = ItemKind.CONTRACT_RULE
    action = _action_creating(state, actor, kind)
    created = next_state(actor, action, kind, None)
    ref = ItemRef(kind, uuid.uuid4().hex)
    with write_transaction(engine) as connection:
        connection.execute(
            text(
                """
                INSERT INTO contract_rule (id, contract_id, rule_type)
                VALUES (:id, :contract, :rule_type)
                """
            ),
            {"id": ref.id, "contract": contract_id, "rule_type": rule_type},
        )
        for position, (role, field_id) in enumerate(fields):
            connection.execute(
                text(
                    """
                    INSERT INTO contract_rule_field (rule_id, role, position, field_id)
                    VALUES (:rule, :role, :position, :field)
                    """
                ),
                {"rule": ref.id, "role": role, "position": position, "field": field_id},
            )
        revision_id = _insert_revision(
            connection, ref, 1, created, _encode(kind, params)
        )
        record_event(
            connection,
            actor,
            action,
            None,
            created,
            _TABLES[kind].event_column,
            revision_id,
            details,
        )
    return ref


def _current(connection: Connection, ref: ItemRef) -> tuple[str, Item]:
    """The latest revision's id and what it says. The latest revision is the current version."""
    tables = _TABLES[ref.kind]
    row = connection.execute(
        text(
            f"""
            SELECT id, state, revision, {tables.content} AS content
            FROM {tables.revisions} WHERE {tables.owner} = :id
            ORDER BY revision DESC LIMIT 1
            """
        ),
        {"id": ref.id},
    ).first()
    if row is None:
        raise ItemNotFound(f"There's no such {ref.kind}.")
    return row.id, Item(
        ref, TrustState(row.state), row.revision, _decode(ref.kind, row.content)
    )


def _entry_subject(connection: Connection, ref: ItemRef) -> EntrySubject:
    row = connection.execute(
        text("SELECT dataset_id, field_id FROM dictionary_entry WHERE id = :id"),
        {"id": ref.id},
    ).one()
    return EntrySubject(row.dataset_id, row.field_id)


def _rule_subject(connection: Connection, ref: ItemRef) -> RuleSubject:
    rule = connection.execute(
        text("SELECT contract_id, rule_type FROM contract_rule WHERE id = :id"),
        {"id": ref.id},
    ).one()
    fields = connection.execute(
        text(
            """
            SELECT role, field_id FROM contract_rule_field
            WHERE rule_id = :id ORDER BY role, position
            """
        ),
        {"id": ref.id},
    ).all()
    return RuleSubject(
        rule.contract_id,
        rule.rule_type,
        tuple((field.role, field.field_id) for field in fields),
    )


def read_item(engine: Engine, ref: ItemRef) -> Item:
    if ref.kind not in _TABLES:
        raise ItemNotFound(f"A {ref.kind} isn't a reviewable item.")
    with engine.connect() as connection:
        item = _current(connection, ref)[1]
        subject = (
            _entry_subject(connection, ref)
            if ref.kind is ItemKind.DICTIONARY_ENTRY
            else _rule_subject(connection, ref)
        )
    return replace(item, subject=subject)


def _latest_action(connection: Connection, ref: ItemRef) -> str:
    tables = _TABLES[ref.kind]
    return connection.execute(
        text(
            f"""
            SELECT provenance_event.action
            FROM provenance_event
            JOIN {tables.revisions} ON provenance_event.{tables.event_column} = {tables.revisions}.id
            WHERE {tables.revisions}.{tables.owner} = :id
            ORDER BY provenance_event.rowid DESC LIMIT 1
            """
        ),
        {"id": ref.id},
    ).scalar_one()


def _check_review_outcome(connection: Connection, ref: ItemRef, action: Action) -> None:
    """Section 12: Reject and Defer take an item out of the inbox, and Deferred ones come back."""
    latest = _latest_action(connection, ref)
    if action is Action.RETURN_TO_INBOX and latest != Action.DEFER.value:
        raise Rejected("Only a deferred item can return to the review inbox.")
    if action in (Action.REJECT, Action.DEFER) and latest in _LEFT_THE_INBOX:
        raise Rejected("This item has already left the review inbox.")


def _apply(
    engine: Engine,
    actor: Actor,
    action: Action,
    ref: ItemRef,
    details: EventDetails,
    new_content: Content | None = None,
) -> None:
    """Check the rules and write the change and its event in one transaction (ADR 0002 point 3)."""
    if ref.kind not in _TABLES:
        raise Rejected(f"A {ref.kind} is written once and can't be reviewed.")
    event_column = _TABLES[ref.kind].event_column
    with write_transaction(engine) as connection:
        revision_id, item = _current(connection, ref)
        state = next_state(actor, action, ref.kind, item.state)
        if action in (Action.REJECT, Action.DEFER, Action.RETURN_TO_INBOX):
            # Neither is a trust state, so only provenance changes (Section 12).
            _check_review_outcome(connection, ref, action)
            record_event(
                connection,
                actor,
                action,
                item.state,
                state,
                event_column,
                revision_id,
                details,
            )
            return
        if (
            ref.kind is ItemKind.DICTIONARY_ENTRY
            and state is TrustState.CONFIRMED
            and item.state is not TrustState.CONFIRMED
        ):
            field_id = _entry_subject(connection, ref).field_id
            if field_id is not None:
                _check_field_has_no_confirmed_entry(connection, field_id)
        content = item.content if new_content is None else new_content
        new_revision_id = _insert_revision(
            connection, ref, item.revision + 1, state, _encode(ref.kind, content)
        )
        record_event(
            connection,
            actor,
            action,
            item.state,
            state,
            event_column,
            new_revision_id,
            details,
            previous_value=None
            if new_content is None
            else json.loads(_encode(ref.kind, item.content)),
        )


def accept(
    engine: Engine, actor: Actor, ref: ItemRef, details: EventDetails = NO_DETAILS
) -> None:
    _apply(engine, actor, Action.ACCEPT, ref, details)


def edit(
    engine: Engine,
    actor: Actor,
    ref: ItemRef,
    content: Content,
    details: EventDetails = NO_DETAILS,
) -> None:
    _apply(engine, actor, Action.EDIT, ref, details, new_content=content)


def promote(
    engine: Engine, actor: Actor, ref: ItemRef, details: EventDetails = NO_DETAILS
) -> None:
    _apply(engine, actor, Action.PROMOTE, ref, details)


def demote(
    engine: Engine, actor: Actor, ref: ItemRef, details: EventDetails = NO_DETAILS
) -> None:
    _apply(engine, actor, Action.DEMOTE, ref, details)


def reject(
    engine: Engine, actor: Actor, ref: ItemRef, details: EventDetails = NO_DETAILS
) -> None:
    _apply(engine, actor, Action.REJECT, ref, details)


def defer(
    engine: Engine, actor: Actor, ref: ItemRef, details: EventDetails = NO_DETAILS
) -> None:
    _apply(engine, actor, Action.DEFER, ref, details)


def return_to_inbox(
    engine: Engine, actor: Actor, ref: ItemRef, details: EventDetails = NO_DETAILS
) -> None:
    _apply(engine, actor, Action.RETURN_TO_INBOX, ref, details)


def review_inbox(engine: Engine) -> list[ItemRef]:
    """Inferred items the user hasn't rejected or deferred, worked out from their history."""
    found: list[ItemRef] = []
    with engine.connect() as connection:
        for kind, tables in _TABLES.items():
            owners = connection.execute(
                text(
                    f"""
                    SELECT latest.{tables.owner}
                    FROM {tables.revisions} AS latest
                    WHERE latest.state = 'inferred'
                    AND latest.revision = (
                        SELECT max(revision) FROM {tables.revisions}
                        WHERE {tables.owner} = latest.{tables.owner}
                    )
                    ORDER BY latest.rowid
                    """
                )
            ).scalars()
            for owner in owners.all():
                ref = ItemRef(kind, owner)
                if _latest_action(connection, ref) not in _LEFT_THE_INBOX:
                    found.append(ref)
    return found


def history(engine: Engine, ref: ItemRef) -> list[Event]:
    """Every event for an item, oldest first."""
    written_once = _WRITTEN_ONCE.get(ref.kind)
    if written_once is not None:
        where = f"provenance_event.{written_once} = :id"
        join = ""
        revision = "NULL"
    else:
        tables = _TABLES[ref.kind]
        where = f"{tables.revisions}.{tables.owner} = :id"
        join = (
            f"JOIN {tables.revisions} "
            f"ON provenance_event.{tables.event_column} = {tables.revisions}.id"
        )
        revision = f"{tables.revisions}.revision"
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT provenance_event.*, {revision} AS item_revision
                FROM provenance_event {join}
                WHERE {where}
                ORDER BY provenance_event.rowid
                """
            ),
            {"id": ref.id},
        ).all()
    return [
        Event(
            author=Actor(row.author),
            occurred_at=row.occurred_at,
            action=Action(row.action),
            from_state=TrustState(row.from_state) if row.from_state else None,
            to_state=TrustState(row.to_state),
            previous_value=json.loads(row.previous_value)
            if row.previous_value
            else None,
            ai_provider=row.ai_provider,
            ai_model=row.ai_model,
            prompt_version=row.prompt_version,
            approved_context_categories=tuple(
                json.loads(row.approved_context_categories)
            ),
            evidence_references=tuple(json.loads(row.evidence_references)),
            reviewer=row.reviewer,
            revision=row.item_revision,
        )
        for row in rows
    ]
