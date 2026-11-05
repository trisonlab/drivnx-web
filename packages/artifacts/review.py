"""Contracts, dictionary entries and contract rules, and the reviewing of them."""

import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from domain.rules import (
    AGREES_WITH,
    check_fields,
    check_params,
    fields_with_role,
    slot,
)
from domain.trust import Action, Actor, ItemKind, Rejected, TrustState, next_state
from sqlalchemy import Connection, Engine, Row, text

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
    # Who or what the item is about, which never changes. `_current` leaves it empty.
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
    # Only a rule can be deleted, so an entry's revision never is a tombstone.
    tombstone: str = "0"


_TABLES = {
    ItemKind.DICTIONARY_ENTRY: _Tables(
        "dictionary_entry_revision",
        "entry_id",
        "content",
        "dictionary_entry_revision_id",
    ),
    ItemKind.CONTRACT_RULE: _Tables(
        "contract_rule_revision",
        "rule_id",
        "params",
        "contract_rule_revision_id",
        "is_tombstone",
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
_LEFT_THE_INBOX = (Action.REJECT.value, Action.DEFER.value, Action.DELETE.value)
# None of these is a change to the item, so only provenance changes (Section 12).
_ONLY_PROVENANCE = (
    Action.REJECT,
    Action.DEFER,
    Action.RETURN_TO_INBOX,
    Action.FLAG_RE_REVIEW,
    Action.KEEP_AS_IS,
)


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
    connection: Connection,
    ref: ItemRef,
    revision: int,
    state: TrustState,
    content: str,
    **rule_columns: int | None,
) -> str:
    tables = _TABLES[ref.kind]
    revision_id = uuid.uuid4().hex
    values = {
        "id": revision_id,
        tables.owner: ref.id,
        "revision": revision,
        "state": state.value,
        tables.content: content,
        **rule_columns,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f":{column}" for column in values)
    connection.execute(
        text(f"INSERT INTO {tables.revisions} ({columns}) VALUES ({placeholders})"),
        values,
    )
    return revision_id


def _new_contract_version(connection: Connection, contract_id: str) -> int:
    """What the contract says is changing, so it gets a new version (ADR 0003 point 3)."""
    version = connection.execute(
        text(
            """
            UPDATE contract SET current_version = current_version + 1
            WHERE id = :id RETURNING current_version
            """
        ),
        {"id": contract_id},
    ).scalar_one()
    connection.execute(
        text(
            """
            INSERT INTO contract_revision (contract_id, version, purpose, limitations)
            SELECT contract_id, :version, purpose, limitations FROM contract_revision
            WHERE contract_id = :id AND version = :version - 1
            """
        ),
        {"id": contract_id, "version": version},
    )
    return version


def _defined_rules_of_type(
    connection: Connection, contract_id: str, rule_type: str
) -> list[ItemRef]:
    """The Defined rules of this type in this contract. Call this holding the write lock."""
    holders = connection.execute(
        text(
            """
            SELECT rule.id FROM contract_rule AS rule
            JOIN contract_rule_revision AS latest ON latest.rule_id = rule.id
            WHERE rule.contract_id = :contract AND rule.rule_type = :rule_type
                AND latest.state = 'defined' AND latest.is_tombstone = 0
                AND latest.revision = (
                    SELECT max(revision) FROM contract_rule_revision
                    WHERE rule_id = rule.id
                )
            """
        ),
        {"contract": contract_id, "rule_type": rule_type},
    ).scalars()
    return [ItemRef(ItemKind.CONTRACT_RULE, holder) for holder in holders]


def _check_slot_is_free(
    connection: Connection,
    contract_id: str,
    rule_type: str,
    fields: Sequence[tuple[str, str]],
) -> None:
    """CFX-APP-first-slice/AC-46 and ADR 0003 point 4, so an export never has to choose."""
    wanted = slot(rule_type, fields)
    for holder in _defined_rules_of_type(connection, contract_id, rule_type):
        if slot(rule_type, _rule_subject(connection, holder).fields) == wanted:
            raise Rejected(f"The contract already has a Defined {rule_type} rule here.")


def _check_logical_types_agree(
    connection: Connection,
    contract_id: str,
    rule_type: str,
    fields: Sequence[tuple[str, str]],
    params: dict[str, Any],
) -> None:
    """ADR 0003 point 4: a range has to match its field's type rule, if there is one."""
    other = AGREES_WITH.get(rule_type)
    if other is None:
        return
    field = slot(rule_type, fields)
    for holder in _defined_rules_of_type(connection, contract_id, other):
        if (
            slot(other, _rule_subject(connection, holder).fields) == field
            and _current(connection, holder)[1].content["logical_type"]
            != params["logical_type"]
        ):
            raise Rejected(f"This field's {other} rule has another logical type.")


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
    check_params(rule_type, params)
    check_fields(rule_type, fields)
    content = _encode(kind, params)
    with write_transaction(engine) as connection:
        _insert_rule(
            connection,
            ref,
            contract_id,
            rule_type,
            fields,
            content,
            actor,
            action,
            created,
            details,
        )
    return ref


def _insert_rule(
    connection: Connection,
    ref: ItemRef,
    contract_id: str,
    rule_type: str,
    fields: Sequence[tuple[str, str]],
    content: str,
    actor: Actor,
    action: Action,
    state: TrustState,
    details: EventDetails,
) -> None:
    """A rule's first revision and event, inside the caller's transaction."""
    _check_fields_are_in_the_right_datasets(connection, contract_id, fields)
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
    revision_id = _insert_revision(connection, ref, 1, state, content)
    record_event(
        connection,
        actor,
        action,
        None,
        state,
        _TABLES[ref.kind].event_column,
        revision_id,
        details,
    )


def _check_fields_are_in_the_right_datasets(
    connection: Connection, contract_id: str, fields: Sequence[tuple[str, str]]
) -> None:
    """Found reviewing T07. A relationship's targets can be elsewhere, but all in one place."""
    contract_dataset = connection.execute(
        text("SELECT dataset_id FROM contract WHERE id = :id"), {"id": contract_id}
    ).scalar()
    if contract_dataset is None:
        raise ValueError("There's no such contract.")

    def datasets_of(role: str) -> set[str | None]:
        return {
            connection.execute(
                text("SELECT dataset_id FROM field WHERE id = :id"), {"id": field}
            ).scalar()
            for field in fields_with_role(fields, role)
        }

    subjects, targets = datasets_of("subject"), datasets_of("target")
    if None in subjects | targets:
        raise ValueError("There's no such field.")
    if subjects != {contract_dataset}:
        raise ValueError("A rule's subject fields must be in its contract's dataset.")
    if len(targets) > 1:
        raise ValueError("A relationship's target fields must all be in one dataset.")


def define_rule(
    engine: Engine,
    actor: Actor,
    contract_id: str,
    rule_type: str,
    fields: Sequence[tuple[str, str]],
    params: dict[str, Any],
    details: EventDetails = NO_DETAILS,
) -> ItemRef:
    """New -> Confirmed -> Defined in one transaction, with both events (ADR 0003 point 2)."""
    kind = ItemKind.CONTRACT_RULE
    confirmed = next_state(actor, Action.CREATE_CONFIRMED, kind, None)
    defined = next_state(actor, Action.PROMOTE, kind, confirmed)
    ref = ItemRef(kind, uuid.uuid4().hex)
    check_params(rule_type, params)
    check_fields(rule_type, fields)
    content = _encode(kind, params)
    with write_transaction(engine) as connection:
        _check_slot_is_free(connection, contract_id, rule_type, fields)
        _check_logical_types_agree(connection, contract_id, rule_type, fields, params)
        _insert_rule(
            connection,
            ref,
            contract_id,
            rule_type,
            fields,
            content,
            actor,
            Action.CREATE_CONFIRMED,
            confirmed,
            details,
        )
        revision_id = _insert_revision(
            connection,
            ref,
            2,
            defined,
            content,
            contract_version=_new_contract_version(connection, contract_id),
        )
        record_event(
            connection,
            actor,
            Action.PROMOTE,
            confirmed,
            defined,
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
            SELECT id, state, revision, {tables.content} AS content,
                   {tables.tombstone} AS tombstone
            FROM {tables.revisions} WHERE {tables.owner} = :id
            ORDER BY revision DESC LIMIT 1
            """
        ),
        {"id": ref.id},
    ).first()
    if row is None:
        raise ItemNotFound(f"There's no such {ref.kind}.")
    if row.tombstone:
        raise ItemNotFound(f"This {ref.kind} has been deleted.")
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


def list_rules(engine: Engine, contract_id: str) -> list[Item]:
    """Every rule of a contract that isn't deleted, candidates included, oldest first."""
    with engine.connect() as connection:
        ids = connection.execute(
            text(
                """
                SELECT rule.id FROM contract_rule AS rule
                JOIN contract_rule_revision AS latest ON latest.rule_id = rule.id
                WHERE rule.contract_id = :contract AND latest.is_tombstone = 0
                    AND latest.revision = (
                        SELECT max(revision) FROM contract_rule_revision
                        WHERE rule_id = rule.id
                    )
                ORDER BY rule.rowid
                """
            ),
            {"contract": contract_id},
        ).scalars()
        found = []
        for rule_id in ids.all():
            ref = ItemRef(ItemKind.CONTRACT_RULE, rule_id)
            found.append(
                replace(
                    _current(connection, ref)[1],
                    subject=_rule_subject(connection, ref),
                )
            )
    return found


def rules_in_contract(
    engine: Engine, contract_id: str, version: int | None = None
) -> list[Item]:
    """The contract at a version, by default its latest (ADR 0003 point 3).

    For each rule, its latest revision at or below that version, if Defined and not a tombstone.
    """
    with engine.connect() as connection:
        current = connection.execute(
            text("SELECT current_version FROM contract WHERE id = :id"),
            {"id": contract_id},
        ).scalar()
        if current is None:
            raise ValueError("There's no such contract.")
        if version is None:
            version = current
        if not 1 <= version <= current:
            raise ValueError("This contract never had that version.")
        rows = connection.execute(
            text(
                """
                SELECT rule.id, revision.state, revision.revision, revision.params
                FROM contract_rule AS rule
                JOIN contract_rule_revision AS revision ON revision.rule_id = rule.id
                WHERE rule.contract_id = :contract
                    AND revision.state = 'defined'
                    AND revision.is_tombstone = 0
                    AND revision.revision = (
                        SELECT max(earlier.revision) FROM contract_rule_revision AS earlier
                        WHERE earlier.rule_id = rule.id
                            AND earlier.contract_version <= :version
                    )
                ORDER BY rule.rowid
                """
            ),
            {"contract": contract_id, "version": version},
        ).all()
        found = []
        for row in rows:
            ref = ItemRef(ItemKind.CONTRACT_RULE, row.id)
            found.append(
                Item(
                    ref,
                    TrustState(row.state),
                    row.revision,
                    _decode(ref.kind, row.params),
                    _rule_subject(connection, ref),
                )
            )
    return found


def _latest_event(connection: Connection, ref: ItemRef) -> Row:
    return _latest(connection, ref, "")


def _latest_decision(connection: Connection, ref: ItemRef) -> str:
    """The latest action, leaving out flags, because a flag isn't anyone's decision."""
    return _latest(connection, ref, "AND provenance_event.action != :flag").action


def _latest_review_or_flag(connection: Connection, ref: ItemRef) -> str:
    """Deferring or returning an item reviews nothing, so neither clears a flag (AC-39)."""
    return _latest(
        connection,
        ref,
        "AND provenance_event.action NOT IN ('defer', 'return_to_inbox')",
    ).action


def _latest(connection: Connection, ref: ItemRef, condition: str) -> Row:
    tables = _TABLES[ref.kind]
    return connection.execute(
        text(
            f"""
            SELECT provenance_event.action, provenance_event.occurred_at
            FROM provenance_event
            JOIN {tables.revisions} ON provenance_event.{tables.event_column} = {tables.revisions}.id
            WHERE {tables.revisions}.{tables.owner} = :id {condition}
            ORDER BY provenance_event.rowid DESC LIMIT 1
            """
        ),
        {"id": ref.id, "flag": Action.FLAG_RE_REVIEW.value},
    ).one()


def _check_review_outcome(connection: Connection, ref: ItemRef, action: Action) -> None:
    """Section 12: Reject and Defer take an item out of the inbox, and Deferred ones come back."""
    latest = _latest_decision(connection, ref)
    if action is Action.FLAG_RE_REVIEW and latest == Action.REJECT.value:
        raise Rejected("A rejected item can't be flagged for re-review.")
    if (
        action is Action.KEEP_AS_IS
        and _latest_event(connection, ref).action != Action.FLAG_RE_REVIEW.value
    ):
        raise Rejected("Only an item flagged for re-review can be kept as it is.")
    if action is Action.RETURN_TO_INBOX and latest != Action.DEFER.value:
        raise Rejected("Only a deferred item can return to the review inbox.")
    if action in (Action.REJECT, Action.DEFER) and latest in _LEFT_THE_INBOX:
        raise Rejected("This item has already left the review inbox.")


def _check_run_can_flag(connection: Connection, ref: ItemRef, run_id: str) -> None:
    """AC-39 says a newer completed run. A run ending before the latest event isn't newer."""
    run = connection.execute(
        text("SELECT status, ended_at FROM profile_run WHERE id = :id"), {"id": run_id}
    ).first()
    if run is None or run.status != "completed":
        raise Rejected("Only a completed profile run can flag an item for re-review.")
    if run.ended_at <= _latest_event(connection, ref).occurred_at:
        raise Rejected("Only a newer profile run can flag this item for re-review.")


def _apply(
    engine: Engine,
    actor: Actor,
    action: Action,
    ref: ItemRef,
    details: EventDetails,
    new_content: Content | None = None,
    flagging_run: str | None = None,
) -> None:
    """Check the rules and write the change and its event in one transaction (ADR 0002 point 3)."""
    if ref.kind not in _TABLES:
        raise Rejected(f"A {ref.kind} is written once and can't be reviewed.")
    event_column = _TABLES[ref.kind].event_column
    with write_transaction(engine) as connection:
        revision_id, item = _current(connection, ref)
        state = next_state(actor, action, ref.kind, item.state)
        if action in _ONLY_PROVENANCE:
            _check_review_outcome(connection, ref, action)
            if flagging_run is not None:
                _check_run_can_flag(connection, ref, flagging_run)
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
        rule_columns = {}
        if ref.kind is ItemKind.CONTRACT_RULE:
            subject = _rule_subject(connection, ref)
            if new_content is not None:
                check_params(subject.rule_type, content)
            if state is TrustState.DEFINED:
                if item.state is not TrustState.DEFINED:
                    _check_slot_is_free(
                        connection,
                        subject.contract_id,
                        subject.rule_type,
                        subject.fields,
                    )
                _check_logical_types_agree(
                    connection,
                    subject.contract_id,
                    subject.rule_type,
                    subject.fields,
                    content,
                )
            if action is Action.DELETE:
                rule_columns["is_tombstone"] = 1
            if TrustState.DEFINED in (item.state, state):
                rule_columns["contract_version"] = _new_contract_version(
                    connection, subject.contract_id
                )
        new_revision_id = _insert_revision(
            connection,
            ref,
            item.revision + 1,
            state,
            _encode(ref.kind, content),
            **rule_columns,
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


def delete_rule(
    engine: Engine, actor: Actor, ref: ItemRef, details: EventDetails = NO_DETAILS
) -> None:
    """A tombstone revision, so a rule never loses rows (ADR 0002 point 4)."""
    _apply(engine, actor, Action.DELETE, ref, details)


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


def flag_for_re_review(
    engine: Engine,
    actor: Actor,
    ref: ItemRef,
    run_id: str,
    details: EventDetails = NO_DETAILS,
) -> None:
    """A newer completed run contradicts this item's evidence. Its state stays (AC-39).

    What counts as a contradiction is the caller's to decide, and comes with M3's profiling work.
    """
    evidence = (*details.evidence_references, f"profile-run/{run_id}")
    _apply(
        engine,
        actor,
        Action.FLAG_RE_REVIEW,
        ref,
        replace(details, evidence_references=evidence),
        flagging_run=run_id,
    )


def keep_as_is(
    engine: Engine, actor: Actor, ref: ItemRef, details: EventDetails = NO_DETAILS
) -> None:
    """A user still agrees with a flagged Confirmed or Defined item, and nothing else changes."""
    _apply(engine, actor, Action.KEEP_AS_IS, ref, details)


def needs_re_review(engine: Engine) -> list[ItemRef]:
    """Items flagged since a user last reviewed them, which leaves out deferring or returning."""
    found: list[ItemRef] = []
    with engine.connect() as connection:
        for kind, tables in _TABLES.items():
            owners = connection.execute(
                text(
                    f"""
                    SELECT {tables.owner} FROM {tables.revisions}
                    GROUP BY {tables.owner} ORDER BY min(rowid)
                    """
                )
            ).scalars()
            for owner in owners.all():
                ref = ItemRef(kind, owner)
                if (
                    _latest_review_or_flag(connection, ref)
                    == Action.FLAG_RE_REVIEW.value
                ):
                    found.append(ref)
    return found


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
                if _latest_decision(connection, ref) not in _LEFT_THE_INBOX:
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
