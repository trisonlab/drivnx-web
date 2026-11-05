"""Contract rules and contract versions, CFX-APP-first-slice AC-45 to AC-47 (ADR 0003)."""

import sqlite3
import threading
import traceback
from collections.abc import Iterator
from pathlib import Path

import pytest
from artifacts.profiles import finish_profile_run, start_profile_run
from artifacts.provenance import NO_DETAILS, EventDetails
from artifacts.review import (
    Contract,
    ItemNotFound,
    ItemRef,
    accept,
    add_contract,
    create_rule,
    defer,
    define_rule,
    delete_rule,
    demote,
    edit,
    flag_for_re_review,
    history,
    keep_as_is,
    list_rules,
    needs_re_review,
    promote,
    read_contract,
    read_item,
    reject,
    return_to_inbox,
    review_inbox,
    rules_in_contract,
)
from artifacts.sources import StoredField, StoredSource, add_csv_source, read_dataset
from artifacts.workspace import Workspace, open_workspace
from domain.trust import Action, Actor, Rejected, TrustState
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ..conftest import RisingClock

# Rule type, subject fields, target fields and params.
Spec = tuple[str, tuple[str, ...], tuple[str, ...], dict]
AI_DETAILS = EventDetails(ai_provider="local", ai_model="m", prompt_version="1")


def everything(workspace: Workspace) -> list[str]:
    connection = sqlite3.connect(workspace.directory / "workspace.db")
    try:
        return list(connection.iterdump())
    finally:
        connection.close()


def add_dataset(workspace: Workspace, source_id: str, *names: str) -> str:
    add_csv_source(
        workspace.engine,
        StoredSource(
            id=source_id,
            path=Path(f"/data/{source_id}.csv"),
            delimiter=",",
            encoding="UTF-8",
            has_header=True,
            size_bytes=10,
            modified_at="2024-01-01T00:00:00+00:00",
            fields=tuple(StoredField(name, "BIGINT", "integer") for name in names),
        ),
    )
    return read_dataset(workspace.engine, source_id).id


class Rules:
    """A contract on a dataset with fields a, b and c, and another dataset with x and y."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.engine = workspace.engine
        dataset = add_dataset(workspace, "customers", "a", "b", "c")
        add_dataset(workspace, "orders", "x", "y")
        self.fields = {
            field.name: field.id
            for source in ("customers", "orders")
            for field in read_dataset(self.engine, source).fields
        }
        self.fields["unknown"] = "no-such-field"
        self.contract_id = add_contract(
            self.engine, dataset, purpose="Billing", limitations="EU only"
        )

    def define(self, rule_type: str = "required", *names: str, **params) -> ItemRef:
        return define_rule(
            self.engine,
            Actor.USER,
            self.contract_id,
            rule_type,
            [("subject", self.fields[name]) for name in names or ("a",)],
            params,
        )

    def candidate(
        self,
        state: TrustState = TrustState.CONFIRMED,
        rule_type: str = "required",
        *names: str,
        **params,
    ) -> ItemRef:
        return create_rule(
            self.engine,
            Actor.USER if state is TrustState.CONFIRMED else Actor.AI,
            self.contract_id,
            rule_type,
            [("subject", self.fields[name]) for name in names or ("a",)],
            params,
            state,
            details=NO_DETAILS if state is TrustState.CONFIRMED else AI_DETAILS,
        )

    def of(self, spec: Spec) -> tuple[str, list[tuple[str, str]], dict]:
        rule_type, subjects, targets, params = spec
        fields = [("subject", self.fields[name]) for name in subjects]
        fields += [("target", self.fields[name]) for name in targets]
        return rule_type, fields, params

    def define_spec(self, spec: Spec) -> ItemRef:
        rule_type, fields, params = self.of(spec)
        return define_rule(
            self.engine, Actor.USER, self.contract_id, rule_type, fields, params
        )

    def candidate_spec(self, spec: Spec) -> ItemRef:
        rule_type, fields, params = self.of(spec)
        return create_rule(
            self.engine,
            Actor.USER,
            self.contract_id,
            rule_type,
            fields,
            params,
            TrustState.CONFIRMED,
        )

    def in_contract(self, version: int | None = None) -> list[ItemRef]:
        return [
            rule.ref
            for rule in rules_in_contract(self.engine, self.contract_id, version)
        ]

    @property
    def contract(self) -> Contract:
        return read_contract(self.engine, self.contract_id)


@pytest.fixture
def rules(tmp_path: Path, clock: RisingClock) -> Iterator[Rules]:
    workspace = open_workspace(tmp_path / "workspace")
    yield Rules(workspace)
    workspace.close()


def test_a_rule_written_directly_goes_new_confirmed_defined(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-47: both events, the second on the Defined revision (ADR 0003 point 2).
    ref = rules.define()

    assert read_item(rules.engine, ref).state is TrustState.DEFINED
    assert read_item(rules.engine, ref).revision == 2
    assert [
        (event.action, event.from_state, event.to_state, event.author, event.revision)
        for event in history(rules.engine, ref)
    ] == [
        (Action.CREATE_CONFIRMED, None, TrustState.CONFIRMED, Actor.USER, 1),
        (Action.PROMOTE, TrustState.CONFIRMED, TrustState.DEFINED, Actor.USER, 2),
    ]


def test_both_events_carry_what_they_were_given(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-47: the evidence behind a rule is on the whole of it.
    ref = define_rule(
        rules.engine,
        Actor.USER,
        rules.contract_id,
        "required",
        [("subject", rules.fields["a"])],
        {},
        EventDetails(evidence_references=("observation/1",)),
    )

    assert [event.evidence_references for event in history(rules.engine, ref)] == [
        ("observation/1",),
        ("observation/1",),
    ]


@pytest.mark.parametrize("actor", [Actor.AI, Actor.SYSTEM])
def test_only_a_person_can_write_a_defined_rule(rules: Rules, actor: Actor) -> None:
    # CFX-APP-first-slice/AC-47, and AC-37: only a person can confirm or define anything.
    before = everything(rules.workspace)

    with pytest.raises(Rejected):
        define_rule(
            rules.engine,
            actor,
            rules.contract_id,
            "required",
            [("subject", rules.fields["a"])],
            {},
        )

    assert everything(rules.workspace) == before


def test_a_failure_on_the_second_step_saves_nothing(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-47: one transaction, so never a Confirmed rule left behind.
    with rules.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TRIGGER no_promote BEFORE INSERT ON provenance_event
                WHEN NEW.action = 'promote'
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
                """
            )
        )
    before = everything(rules.workspace)

    with pytest.raises(DBAPIError, match="forced failure"):
        rules.define()

    assert everything(rules.workspace) == before


def test_a_rule_written_directly_is_in_a_new_contract_version(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-47 and ADR 0003 point 3.
    ref = rules.define()

    assert rules.contract.version == 2
    assert rules.in_contract() == [ref]
    assert rules.in_contract(1) == []


def test_promoting_a_rule_creates_a_contract_version(rules: Rules) -> None:
    # ADR 0003 point 3: a rule becoming Defined.
    ref = rules.candidate()

    promote(rules.engine, Actor.USER, ref)

    assert rules.contract.version == 2
    assert rules.in_contract() == [ref]


def test_a_new_version_keeps_the_purpose_and_limitations(rules: Rules) -> None:
    # ADR 0003 point 3: a new version changes only what caused it.
    rules.define()

    assert (rules.contract.purpose, rules.contract.limitations) == (
        "Billing",
        "EU only",
    )


def test_editing_a_defined_rule_creates_a_contract_version(rules: Rules) -> None:
    # ADR 0003 point 3. The old version still says what it said.
    ref = rules.define("accepted_values", "a", values=["red"])

    edit(rules.engine, Actor.USER, ref, {"values": ["blue"]})

    assert rules.contract.version == 3
    now, before = [
        rules_in_contract(rules.engine, rules.contract_id, version)[0].content
        for version in (3, 2)
    ]
    assert now == {"values": ["blue"]}
    assert before == {"values": ["red"]}


def test_a_demoted_rule_leaves_the_contract_as_confirmed(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-45, and an old version can still be exported (ADR 0003 point 3).
    ref = rules.define()

    demote(rules.engine, Actor.USER, ref)

    assert read_item(rules.engine, ref).state is TrustState.CONFIRMED
    assert rules.contract.version == 3
    assert rules.in_contract() == []
    assert rules.in_contract(2) == [ref]


def test_demoting_keeps_a_rules_history(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-45: its revisions and events stay, with one of each added.
    ref = rules.define()
    before = history(rules.engine, ref)

    demote(rules.engine, Actor.USER, ref)

    after = history(rules.engine, ref)
    assert after[:-1] == before
    assert (after[-1].action, after[-1].from_state, after[-1].to_state) == (
        Action.DEMOTE,
        TrustState.DEFINED,
        TrustState.CONFIRMED,
    )
    assert after[-1].revision == 3


def test_a_rule_defined_again_comes_back_in_a_new_version(rules: Rules) -> None:
    # ADR 0003 point 3, worked by hand: in at 2, out at 3, in at 4.
    ref = rules.define()
    demote(rules.engine, Actor.USER, ref)

    promote(rules.engine, Actor.USER, ref)

    assert [rules.in_contract(version) for version in (1, 2, 3, 4)] == [
        [],
        [ref],
        [],
        [ref],
    ]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda r, c: None, id="creating one"),
        pytest.param(lambda r, c: accept(r.engine, Actor.USER, c), id="accepting one"),
        pytest.param(lambda r, c: edit(r.engine, Actor.USER, c, {}), id="editing one"),
        pytest.param(lambda r, c: reject(r.engine, Actor.USER, c), id="rejecting one"),
        pytest.param(lambda r, c: defer(r.engine, Actor.USER, c), id="deferring one"),
    ],
)
def test_changing_a_candidate_creates_no_contract_version(rules: Rules, change) -> None:
    # CFX-APP-first-slice/AC-46, on an Inferred rule.
    candidate = rules.candidate(TrustState.INFERRED)

    change(rules, candidate)

    assert rules.contract.version == 1
    assert rules.in_contract() == []


def test_demoting_a_confirmed_rule_creates_no_contract_version(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-46: it was never in the contract.
    candidate = rules.candidate()
    edit(rules.engine, Actor.USER, candidate, {})

    demote(rules.engine, Actor.USER, candidate)

    assert rules.contract.version == 1


def test_a_flag_on_a_defined_rule_creates_no_contract_version(rules: Rules) -> None:
    # AC-39 keeps the item as it is, so what the contract says hasn't changed.
    ref = rules.define()
    run = start_profile_run(rules.engine, scope={}, mode="full")
    finish_profile_run(rules.engine, run, "completed")

    flag_for_re_review(rules.engine, Actor.SYSTEM, ref, run)
    keep_as_is(rules.engine, Actor.USER, ref)

    assert rules.contract.version == 2


def tombstones(rules: Rules, ref: ItemRef) -> list[tuple[int, int]]:
    with rules.engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT revision, is_tombstone FROM contract_rule_revision
                    WHERE rule_id = :id ORDER BY revision
                    """
                ),
                {"id": ref.id},
            )
        ]


def test_deleting_a_rule_writes_a_tombstone_and_removes_no_rows(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-45: every row from before is still there, unchanged.
    ref = rules.candidate()
    before = everything(rules.workspace)

    delete_rule(rules.engine, Actor.USER, ref)

    assert set(before) <= set(everything(rules.workspace))
    assert tombstones(rules, ref) == [(1, 0), (2, 1)]
    deleted = history(rules.engine, ref)[-1]
    assert (deleted.action, deleted.from_state, deleted.to_state, deleted.revision) == (
        Action.DELETE,
        TrustState.CONFIRMED,
        TrustState.CONFIRMED,
        2,
    )


def test_deleting_a_defined_rule_takes_it_out_in_a_new_version(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-45 and ADR 0003 point 3. The old version still has it.
    ref = rules.define()

    delete_rule(rules.engine, Actor.USER, ref)

    assert rules.contract.version == 3
    assert rules.in_contract() == []
    assert rules.in_contract(2) == [ref]
    assert tombstones(rules, ref) == [(1, 0), (2, 0), (3, 1)]


@pytest.mark.parametrize("state", [TrustState.INFERRED, TrustState.CONFIRMED])
def test_deleting_a_candidate_creates_no_contract_version(
    rules: Rules, state: TrustState
) -> None:
    # CFX-APP-first-slice/AC-46: it was never in the contract.
    ref = rules.candidate(state)

    delete_rule(rules.engine, Actor.USER, ref)

    assert rules.contract.version == 1


@pytest.mark.parametrize("actor", [Actor.AI, Actor.SYSTEM])
def test_only_a_person_can_delete_a_rule(rules: Rules, actor: Actor) -> None:
    # CFX-APP-first-slice/AC-45, and AC-37: this takes a rule out of the contract.
    ref = rules.define()
    before = everything(rules.workspace)

    with pytest.raises(Rejected):
        delete_rule(rules.engine, actor, ref)

    assert everything(rules.workspace) == before


def test_a_failure_while_deleting_saves_nothing(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-45 and ADR 0002 point 3: the version, the tombstone and its event.
    ref = rules.define()
    with rules.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TRIGGER no_delete BEFORE INSERT ON provenance_event
                WHEN NEW.action = 'delete'
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
                """
            )
        )
    before = everything(rules.workspace)

    with pytest.raises(DBAPIError, match="forced failure"):
        delete_rule(rules.engine, Actor.USER, ref)

    assert everything(rules.workspace) == before


def test_a_deleted_rule_cant_be_read_as_current_but_keeps_its_history(
    rules: Rules,
) -> None:
    # CFX-APP-first-slice/AC-45: its history stays intact (ADR 0002 point 4).
    ref = rules.candidate()
    delete_rule(rules.engine, Actor.USER, ref)

    with pytest.raises(ItemNotFound, match="deleted"):
        read_item(rules.engine, ref)

    assert [event.action for event in history(rules.engine, ref)] == [
        Action.CREATE_CONFIRMED,
        Action.DELETE,
    ]


AFTER_DELETING = {
    "accept": lambda e, ref: accept(e, Actor.USER, ref),
    "edit": lambda e, ref: edit(e, Actor.USER, ref, {}),
    "promote": lambda e, ref: promote(e, Actor.USER, ref),
    "demote": lambda e, ref: demote(e, Actor.USER, ref),
    "reject": lambda e, ref: reject(e, Actor.USER, ref),
    "defer": lambda e, ref: defer(e, Actor.USER, ref),
    "return": lambda e, ref: return_to_inbox(e, Actor.USER, ref),
    "keep": lambda e, ref: keep_as_is(e, Actor.USER, ref),
    "delete": lambda e, ref: delete_rule(e, Actor.USER, ref),
}


@pytest.mark.parametrize("action", AFTER_DELETING)
def test_nothing_can_be_done_to_a_deleted_rule(rules: Rules, action: str) -> None:
    # CFX-APP-first-slice/AC-45: pointing it at something else means a new rule (ADR 0003 point 2).
    ref = rules.candidate()
    delete_rule(rules.engine, Actor.USER, ref)
    before = everything(rules.workspace)

    with pytest.raises(ItemNotFound, match="deleted"):
        AFTER_DELETING[action](rules.engine, ref)

    assert everything(rules.workspace) == before


def test_a_deleted_rule_cant_be_flagged(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-39: nobody could ever clear it.
    ref = rules.candidate()
    delete_rule(rules.engine, Actor.USER, ref)
    run = start_profile_run(rules.engine, scope={}, mode="full")
    finish_profile_run(rules.engine, run, "completed")

    with pytest.raises(ItemNotFound, match="deleted"):
        flag_for_re_review(rules.engine, Actor.SYSTEM, ref, run)


def test_a_deleted_rule_leaves_the_review_inbox(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-38: an Inferred tombstone keeps its state, and nobody can review it.
    ref = rules.candidate(TrustState.INFERRED)
    assert review_inbox(rules.engine) == [ref]

    delete_rule(rules.engine, Actor.USER, ref)

    assert review_inbox(rules.engine) == []


def test_deleting_a_flagged_rule_clears_the_flag(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-39: nothing is left to review.
    ref = rules.define()
    run = start_profile_run(rules.engine, scope={}, mode="full")
    finish_profile_run(rules.engine, run, "completed")
    flag_for_re_review(rules.engine, Actor.SYSTEM, ref, run)

    delete_rule(rules.engine, Actor.USER, ref)

    assert needs_re_review(rules.engine) == []


INTEGER_RANGE = {"logical_type": "integer", "minimum": 1}
# ADR 0003 point 4, a row of its table each: a rule, and a second that competes with it.
SAME_SLOT = {
    "required, one field": (
        ("required", ("a",), (), {}),
        ("required", ("a",), (), {}),
    ),
    "type, one field": (
        ("type", ("a",), (), {"logical_type": "string"}),
        ("type", ("a",), (), {"logical_type": "integer"}),
    ),
    "unique, identical field set": (
        ("unique", ("a", "b"), (), {}),
        ("unique", ("b", "a"), (), {}),
    ),
    "accepted values, one field": (
        ("accepted_values", ("a",), (), {"values": ["red"]}),
        ("accepted_values", ("a",), (), {"values": ["blue"]}),
    ),
    "range, one field": (
        ("range", ("a",), (), INTEGER_RANGE),
        ("range", ("a",), (), {"logical_type": "integer", "maximum": 9}),
    ),
    "identifier, the whole contract": (
        ("identifier", ("a",), (), {}),
        ("identifier", ("b", "c"), (), {}),
    ),
    "relationship, identical": (
        ("relationship", ("a",), ("x",), {}),
        ("relationship", ("a",), ("x",), {}),
    ),
    "relationship, the same pairs in another order": (
        ("relationship", ("a", "b"), ("x", "y"), {}),
        ("relationship", ("b", "a"), ("y", "x"), {}),
    ),
}
OTHER_SLOT = {
    "another field": (
        ("required", ("a",), (), {}),
        ("required", ("b",), (), {}),
    ),
    "another type on one field": (
        ("required", ("a",), (), {}),
        ("unique", ("a",), (), {}),
    ),
    "unique, a larger set": (
        ("unique", ("a",), (), {}),
        ("unique", ("a", "b"), (), {}),
    ),
    "relationship, another target": (
        ("relationship", ("a",), ("x",), {}),
        ("relationship", ("a",), ("y",), {}),
    ),
    "relationship, another subject": (
        ("relationship", ("a",), ("x",), {}),
        ("relationship", ("b",), ("x",), {}),
    ),
    "relationship, the same fields paired differently": (
        ("relationship", ("a", "b"), ("x", "y"), {}),
        ("relationship", ("a", "b"), ("y", "x"), {}),
    ),
}


@pytest.mark.parametrize(("first", "second"), SAME_SLOT.values(), ids=SAME_SLOT)
def test_promoting_into_an_occupied_slot_is_rejected(
    rules: Rules, first: Spec, second: Spec
) -> None:
    # CFX-APP-first-slice/AC-46
    rules.define_spec(first)
    candidate = rules.candidate_spec(second)
    before = everything(rules.workspace)

    with pytest.raises(Rejected, match="already"):
        promote(rules.engine, Actor.USER, candidate)

    assert everything(rules.workspace) == before


@pytest.mark.parametrize(("first", "second"), SAME_SLOT.values(), ids=SAME_SLOT)
def test_writing_a_defined_rule_into_an_occupied_slot_is_rejected(
    rules: Rules, first: Spec, second: Spec
) -> None:
    # CFX-APP-first-slice/AC-46 and CFX-APP-first-slice/AC-47: the direct way in has the same rule.
    rules.define_spec(first)
    before = everything(rules.workspace)

    with pytest.raises(Rejected, match="already"):
        rules.define_spec(second)

    assert everything(rules.workspace) == before


@pytest.mark.parametrize(("first", "second"), OTHER_SLOT.values(), ids=OTHER_SLOT)
def test_rules_in_different_slots_can_both_be_defined(
    rules: Rules, first: Spec, second: Spec
) -> None:
    # CFX-APP-first-slice/AC-46: the other half, or refusing everything would pass the test above.
    refs = [rules.define_spec(first), rules.define_spec(second)]

    assert rules.in_contract() == refs


@pytest.mark.parametrize(("first", "second"), SAME_SLOT.values(), ids=SAME_SLOT)
def test_overlapping_candidates_are_allowed(
    rules: Rules, first: Spec, second: Spec
) -> None:
    # CFX-APP-first-slice/AC-46, also next to a Defined rule in that slot.
    rules.define_spec(first)
    version = rules.contract.version

    rules.candidate_spec(first)
    rules.candidate_spec(second)

    assert rules.contract.version == version


def test_a_rule_in_another_contract_does_not_hold_the_slot(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-46: an identifier's slot is one contract, not all of them.
    rules.define_spec(("identifier", ("a",), (), {}))
    other = add_contract(
        rules.engine,
        read_dataset(rules.engine, "orders").id,
        purpose="Orders",
        limitations="",
    )

    ref = define_rule(
        rules.engine,
        Actor.USER,
        other,
        "identifier",
        [("subject", rules.fields["x"])],
        {},
    )

    assert read_item(rules.engine, ref).state is TrustState.DEFINED


@pytest.mark.parametrize(
    "leave",
    [
        pytest.param(demote, id="demoted"),
        pytest.param(delete_rule, id="deleted"),
    ],
)
def test_a_rule_leaving_the_contract_frees_its_slot(rules: Rules, leave) -> None:
    # CFX-APP-first-slice/AC-46: what holds a slot is being in the contract.
    first = rules.define()
    second = rules.candidate()
    leave(rules.engine, Actor.USER, first)

    promote(rules.engine, Actor.USER, second)

    assert rules.in_contract() == [second]


def test_a_rule_doesnt_compete_with_itself_when_edited(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-46: its fields never change, so an edit keeps its slot.
    ref = rules.define("accepted_values", "a", values=["red"])

    edit(rules.engine, Actor.USER, ref, {"values": ["blue"]})

    assert rules.in_contract() == [ref]


def test_two_promotions_at_once_cant_both_take_a_slot(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-46: the check runs holding the write lock (ADR 0003 point 4).
    candidates = [rules.candidate() for _ in range(8)]
    start = threading.Barrier(len(candidates))
    outcomes: list[str] = []

    def race(candidate: ItemRef) -> None:
        start.wait()
        try:
            promote(rules.engine, Actor.USER, candidate)
            outcomes.append("promoted")
        except Rejected:
            outcomes.append("rejected")

    threads = [threading.Thread(target=race, args=(c,)) for c in candidates]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert sorted(outcomes) == ["promoted", *["rejected"] * 7]
    assert len(rules.in_contract()) == 1


# ADR 0003 point 4, the Target column. Fields are the contract's a, b, c and another dataset's x, y.
BAD_FIELDS = {
    "required, no field": ("required", (), (), {}),
    "required, two fields": ("required", ("a", "b"), (), {}),
    "required, with a target": ("required", ("a",), ("x",), {}),
    "type, two fields": ("type", ("a", "b"), (), {"logical_type": "string"}),
    "accepted values, two fields": (
        "accepted_values",
        ("a", "b"),
        (),
        {"values": ["red"]},
    ),
    "range, two fields": ("range", ("a", "b"), (), INTEGER_RANGE),
    "unique, no field": ("unique", (), (), {}),
    "unique, one field twice": ("unique", ("a", "a"), (), {}),
    "identifier, no field": ("identifier", (), (), {}),
    "identifier, one field twice": ("identifier", ("a", "a"), (), {}),
    "relationship, no fields": ("relationship", (), (), {}),
    "relationship, no target": ("relationship", ("a",), (), {}),
    "relationship, no subject": ("relationship", (), ("x",), {}),
    "relationship, different counts": ("relationship", ("a", "b"), ("x",), {}),
    "relationship, one subject twice": ("relationship", ("a", "a"), ("x", "y"), {}),
    "relationship, one target twice": ("relationship", ("a", "b"), ("x", "x"), {}),
    "relationship, targets in two datasets": (
        "relationship",
        ("a", "b"),
        ("x", "c"),
        {},
    ),
    "subject from another dataset": ("required", ("x",), (), {}),
    "one of its subjects from another dataset": ("unique", ("a", "x"), (), {}),
    "relationship, subject from another dataset": (
        "relationship",
        ("x",),
        ("a",),
        {},
    ),
    "unknown subject": ("required", ("unknown",), (), {}),
    "unknown target": ("relationship", ("a",), ("unknown",), {}),
}
WAYS_TO_WRITE = {
    "as a candidate": Rules.candidate_spec,
    "directly": Rules.define_spec,
}


@pytest.mark.parametrize("write", WAYS_TO_WRITE)
@pytest.mark.parametrize("spec", BAD_FIELDS.values(), ids=BAD_FIELDS)
def test_a_rules_fields_must_fit_its_type_and_dataset(
    rules: Rules, spec: Spec, write: str
) -> None:
    # Found reviewing T07, and ADR 0003 point 4. A `required` rule with no fields reached Defined.
    before = everything(rules.workspace)

    with pytest.raises(ValueError, match="field"):
        WAYS_TO_WRITE[write](rules, spec)

    assert everything(rules.workspace) == before


@pytest.mark.parametrize(
    "roles",
    [
        pytest.param({"a": "source"}, id="only one it doesn't know"),
        pytest.param({"a": "subject", "b": "source"}, id="one of each"),
    ],
)
def test_a_field_needs_a_role_a_rule_knows(rules: Rules, roles: dict[str, str]) -> None:
    # ADR 0003 point 2: a raw database error otherwise.
    before = everything(rules.workspace)

    with pytest.raises(ValueError, match="subject or its target"):
        create_rule(
            rules.engine,
            Actor.USER,
            rules.contract_id,
            "required",
            [(role, rules.fields[name]) for name, role in roles.items()],
            {},
            TrustState.CONFIRMED,
        )

    assert everything(rules.workspace) == before


def test_a_relationship_can_point_within_its_own_dataset(rules: Rules) -> None:
    # ADR 0003 point 4 only asks that its targets are all in one dataset.
    ref = rules.define_spec(("relationship", ("a",), ("b",), {}))

    assert rules.in_contract() == [ref]


def test_a_relationships_fields_read_back_in_order(rules: Rules) -> None:
    # ADR 0003 point 4: an identifier's are ordered too, and the pairs depend on this.
    ref = rules.define_spec(("relationship", ("b", "a"), ("y", "x"), {}))

    fields = rules.fields
    assert read_item(rules.engine, ref).subject.fields == (
        ("subject", fields["b"]),
        ("subject", fields["a"]),
        ("target", fields["y"]),
        ("target", fields["x"]),
    )


def test_a_rule_needs_a_contract(rules: Rules) -> None:
    # A raw foreign key error otherwise.
    with pytest.raises(ValueError, match="no such contract"):
        create_rule(
            rules.engine,
            Actor.USER,
            "no-such-contract",
            "required",
            [("subject", rules.fields["a"])],
            {},
            TrustState.CONFIRMED,
        )


# ADR 0003 point 4, the Parameters column, on one field.
GOOD_PARAMS = {
    "required": ("required", {}),
    "type": ("type", {"logical_type": "string"}),
    "unique": ("unique", {}),
    "identifier": ("identifier", {}),
    "accepted values of each scalar": (
        "accepted_values",
        {"values": ["red", 1, 2.5, True]},
    ),
    "accepted values, one": ("accepted_values", {"values": ["red"]}),
    "range, a number": ("range", {"logical_type": "number", "minimum": 0.5}),
    "range, a whole number as a number": (
        "range",
        {"logical_type": "number", "maximum": 5},
    ),
    "range, an integer": ("range", {"logical_type": "integer", "maximum": 9}),
    "range, both ends": (
        "range",
        {"logical_type": "integer", "minimum": 1, "maximum": 9},
    ),
    "range, one value": (
        "range",
        {"logical_type": "integer", "minimum": 3, "maximum": 3},
    ),
    "range, a date": ("range", {"logical_type": "date", "minimum": "2024-01-31"}),
    "range, a timestamp": (
        "range",
        {"logical_type": "timestamp", "maximum": "2024-01-31T10:00:00+00:00"},
    ),
    "range, a timestamp with a space": (
        "range",
        {"logical_type": "timestamp", "minimum": "2024-01-31 10:00:00"},
    ),
    "range, a timestamp with a lower-case t": (
        "range",
        {"logical_type": "timestamp", "minimum": "2024-01-31t10:00:00"},
    ),
    "range, a timestamp in basic form": (
        "range",
        {"logical_type": "timestamp", "minimum": "20240131T100000"},
    ),
    "range, a date as a timestamp": (
        "range",
        {"logical_type": "timestamp", "minimum": "2024-01-31"},
    ),
    "range, a time": ("range", {"logical_type": "time", "minimum": "10:00:00"}),
}
BAD_PARAMS = {
    "required, with params": ("required", {"note": "x"}),
    "required, its type again": ("required", {"rule_type": "required"}),
    "unique, with params": ("unique", {"note": "x"}),
    "identifier, with params": ("identifier", {"note": "x"}),
    "type, none": ("type", {}),
    "type, unknown": ("type", {"logical_type": "text"}),
    "type, with more": ("type", {"logical_type": "string", "note": "x"}),
    "accepted values, none given": ("accepted_values", {}),
    "accepted values, empty": ("accepted_values", {"values": []}),
    "accepted values, not a list": ("accepted_values", {"values": "red"}),
    "accepted values, repeated": ("accepted_values", {"values": ["red", "red"]}),
    "accepted values, one number twice": ("accepted_values", {"values": [1, 1.0]}),
    "accepted values, a null": ("accepted_values", {"values": [None]}),
    "accepted values, a list": ("accepted_values", {"values": [["red"]]}),
    "accepted values, not a number": ("accepted_values", {"values": [float("nan")]}),
    # Half a character, which JSON can carry in and a response can't send back.
    "accepted values, a lone surrogate": ("accepted_values", {"values": ["\ud83d"]}),
    "accepted values, a set": ("accepted_values", {"values": {"red"}}),
    "accepted values, bytes": ("accepted_values", {"values": [b"red"]}),
    "required, a key that isn't text": ("required", {("note",): "x"}),
    "range, no ends": ("range", {"logical_type": "integer"}),
    "range, no type": ("range", {"minimum": 1}),
    "range, a type ODCS refuses": ("range", {"logical_type": "string", "minimum": "a"}),
    "range, a fraction as an integer": (
        "range",
        {"logical_type": "integer", "minimum": 1.5},
    ),
    "range, true as an integer": (
        "range",
        {"logical_type": "integer", "minimum": True},
    ),
    "range, text as a number": ("range", {"logical_type": "number", "minimum": "1"}),
    "range, infinity": ("range", {"logical_type": "number", "maximum": float("inf")}),
    "range, not a date": ("range", {"logical_type": "date", "minimum": "soon"}),
    "range, a number as a date": ("range", {"logical_type": "date", "minimum": 5}),
    # These read as a date, a timestamp and a time if they're ever turned into text first.
    "range, digits as a date": (
        "range",
        {"logical_type": "date", "minimum": 20240131},
    ),
    "range, digits as a timestamp": (
        "range",
        {"logical_type": "timestamp", "minimum": 20240131},
    ),
    "range, digits as a time": ("range", {"logical_type": "time", "minimum": 1000}),
    "range, a timestamp as a date": (
        "range",
        {"logical_type": "date", "minimum": "2024-01-31T10:00:00"},
    ),
    "range, a timestamp with another separator": (
        "range",
        {"logical_type": "timestamp", "minimum": "2024-01-31x10:00:00"},
    ),
    "range, not a timestamp": (
        "range",
        {"logical_type": "timestamp", "minimum": "soon"},
    ),
    "range, not a time": ("range", {"logical_type": "time", "minimum": "soon"}),
    "range, backwards": (
        "range",
        {"logical_type": "integer", "minimum": 9, "maximum": 1},
    ),
    "range, dates backwards": (
        "range",
        {"logical_type": "date", "minimum": "2024-02-01", "maximum": "2024-01-01"},
    ),
    "range, timestamps that can't be compared": (
        "range",
        {
            "logical_type": "timestamp",
            "minimum": "2024-01-01T10:00:00",
            "maximum": "2024-01-01T10:00:00+00:00",
        },
    ),
}


@pytest.mark.parametrize(("rule_type", "params"), GOOD_PARAMS.values(), ids=GOOD_PARAMS)
def test_params_that_fit_their_type_read_back(
    rules: Rules, rule_type: str, params: dict
) -> None:
    # CFX-APP-first-slice/AC-35 and ADR 0003 point 2: a rule says exactly what it was given.
    ref = rules.define_spec((rule_type, ("a",), (), params))

    assert read_item(rules.engine, ref).content == params


@pytest.mark.parametrize("write", WAYS_TO_WRITE)
@pytest.mark.parametrize(("rule_type", "params"), BAD_PARAMS.values(), ids=BAD_PARAMS)
def test_params_that_dont_fit_are_refused(
    rules: Rules, rule_type: str, params: dict, write: str
) -> None:
    # ADR 0003 points 2 and 4, on the way in.
    before = everything(rules.workspace)

    with pytest.raises(ValueError, match="rule's params"):
        WAYS_TO_WRITE[write](rules, (rule_type, ("a",), (), params))

    assert everything(rules.workspace) == before


@pytest.mark.parametrize(("rule_type", "params"), BAD_PARAMS.values(), ids=BAD_PARAMS)
def test_an_edit_cant_give_a_rule_params_that_dont_fit(
    rules: Rules, rule_type: str, params: dict
) -> None:
    # ADR 0003 points 2 and 4: otherwise a rule could be written well and then edited.
    good = next(
        good for good_type, good in GOOD_PARAMS.values() if good_type == rule_type
    )
    ref = rules.candidate_spec((rule_type, ("a",), (), good))
    before = everything(rules.workspace)

    with pytest.raises(ValueError, match="rule's params"):
        edit(rules.engine, Actor.USER, ref, params)

    assert everything(rules.workspace) == before


@pytest.mark.parametrize(
    "logical_type",
    [
        "string",
        "date",
        "timestamp",
        "time",
        "number",
        "integer",
        "object",
        "array",
        "boolean",
        "map",
        "vector",
    ],
)
def test_a_type_rule_takes_every_logical_type_odcs_lists(
    rules: Rules, logical_type: str
) -> None:
    # ADR 0003 point 4. The list is written out from ODCS v3.2.0's JSON Schema.
    params = {"logical_type": logical_type}

    ref = rules.define_spec(("type", ("a",), (), params))

    assert read_item(rules.engine, ref).content == params


def test_an_unknown_rule_type_is_refused(rules: Rules) -> None:
    # ADR 0003 point 4 lists seven. A raw database error otherwise.
    with pytest.raises(ValueError, match="rule's params"):
        rules.candidate_spec(("nonsense", ("a",), (), {}))


@pytest.mark.parametrize(
    ("rule_type", "params"),
    [
        ("accepted_values", {"values": ["Jane Doe", "Jane Doe"]}),
        ("range", {"logical_type": "date", "minimum": "Jane Doe"}),
        ("required", {"Jane": "Jane Doe"}),
    ],
)
def test_a_refusal_doesnt_repeat_the_values(
    rules: Rules, rule_type: str, params: dict
) -> None:
    # Section 7 and ADR 0003: accepted values and range ends can be copied from source data.
    with pytest.raises(ValueError) as refusal:
        rules.candidate_spec((rule_type, ("a",), (), params))

    # A traceback in a log is where a value would really get out, and whatever reads the exception
    # next could follow __context__ to pydantic's error, which repeats its input.
    assert "Jane Doe" not in "".join(traceback.format_exception(refusal.value))
    assert refusal.value.__context__ is None


INTEGER_TYPE = {"logical_type": "integer"}
STRING_TYPE = {"logical_type": "string"}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(
            ("range", ("a",), (), INTEGER_RANGE),
            ("type", ("a",), (), STRING_TYPE),
            id="a type after a range",
        ),
        pytest.param(
            ("type", ("a",), (), STRING_TYPE),
            ("range", ("a",), (), INTEGER_RANGE),
            id="a range after a type",
        ),
    ],
)
def test_a_range_must_match_the_fields_type_rule(
    rules: Rules, first: Spec, second: Spec
) -> None:
    # ADR 0003 point 4: they'd both say what the one logicalType in the export is.
    rules.define_spec(first)
    candidate = rules.candidate_spec(second)
    before = everything(rules.workspace)

    with pytest.raises(Rejected, match="logical type"):
        promote(rules.engine, Actor.USER, candidate)
    with pytest.raises(Rejected, match="logical type"):
        rules.define_spec(second)

    assert everything(rules.workspace) == before


def test_a_range_and_type_that_agree_can_both_be_defined(rules: Rules) -> None:
    # ADR 0003 point 4: the other half.
    refs = [
        rules.define_spec(("range", ("a",), (), INTEGER_RANGE)),
        rules.define_spec(("type", ("a",), (), INTEGER_TYPE)),
    ]

    assert rules.in_contract() == refs


def test_a_range_is_only_held_to_its_own_fields_type(rules: Rules) -> None:
    # ADR 0003 point 4 says the field's type rule, not any type rule.
    rules.define_spec(("type", ("a",), (), STRING_TYPE))

    ref = rules.define_spec(("range", ("b",), (), INTEGER_RANGE))

    assert read_item(rules.engine, ref).state is TrustState.DEFINED


@pytest.mark.parametrize(
    ("first", "second", "changed"),
    [
        pytest.param(
            ("range", ("a",), (), INTEGER_RANGE),
            ("type", ("a",), (), INTEGER_TYPE),
            STRING_TYPE,
            id="editing the type",
        ),
        pytest.param(
            ("type", ("a",), (), INTEGER_TYPE),
            ("range", ("a",), (), INTEGER_RANGE),
            {"logical_type": "number", "minimum": 1},
            id="editing the range",
        ),
    ],
)
def test_editing_a_defined_rule_cant_break_the_match(
    rules: Rules, first: Spec, second: Spec, changed: dict
) -> None:
    # ADR 0003 point 4: otherwise an edit would get round the check on promoting.
    rules.define_spec(first)
    ref = rules.define_spec(second)
    before = everything(rules.workspace)

    with pytest.raises(Rejected, match="logical type"):
        edit(rules.engine, Actor.USER, ref, changed)

    assert everything(rules.workspace) == before


def test_candidates_can_disagree_about_a_type(rules: Rules) -> None:
    # CFX-APP-first-slice/AC-46: only Defined rules make the export say anything.
    rules.define_spec(("type", ("a",), (), STRING_TYPE))
    candidate = rules.candidate_spec(("range", ("a",), (), INTEGER_RANGE))

    edit(
        rules.engine,
        Actor.USER,
        candidate,
        {"logical_type": "number", "minimum": 2},
    )

    assert read_item(rules.engine, candidate).state is TrustState.CONFIRMED


def test_a_demoted_type_rule_no_longer_holds_a_range_to_it(rules: Rules) -> None:
    # ADR 0003 point 4: only a type rule in the contract counts.
    type_rule = rules.define_spec(("type", ("a",), (), STRING_TYPE))
    demote(rules.engine, Actor.USER, type_rule)

    ref = rules.define_spec(("range", ("a",), (), INTEGER_RANGE))

    assert rules.in_contract() == [ref]


def test_listing_a_contracts_rules_gives_every_rule_not_deleted(rules: Rules) -> None:
    # Found reviewing T07: a rule could only be read by its id. Candidates are listed too.
    defined = rules.define()
    confirmed = rules.candidate()
    inferred = rules.candidate(TrustState.INFERRED)
    deleted = rules.candidate()
    delete_rule(rules.engine, Actor.USER, deleted)

    listed = list_rules(rules.engine, rules.contract_id)

    assert [(rule.ref, rule.state) for rule in listed] == [
        (defined, TrustState.DEFINED),
        (confirmed, TrustState.CONFIRMED),
        (inferred, TrustState.INFERRED),
    ]
    assert listed == [read_item(rules.engine, rule.ref) for rule in listed]


def test_listing_shows_a_rules_latest_revision(rules: Rules) -> None:
    # Found reviewing T07: a rule's current version is its latest revision (ADR 0002 point 4).
    ref = rules.candidate(TrustState.CONFIRMED, "accepted_values", "a", values=["red"])
    edit(rules.engine, Actor.USER, ref, {"values": ["blue"]})

    assert [rule.content for rule in list_rules(rules.engine, rules.contract_id)] == [
        {"values": ["blue"]}
    ]


def test_listing_leaves_out_another_contracts_rules(rules: Rules) -> None:
    # Found reviewing T07.
    rules.define()
    other = add_contract(
        rules.engine,
        read_dataset(rules.engine, "orders").id,
        purpose="Orders",
        limitations="",
    )

    assert list_rules(rules.engine, other) == []


@pytest.mark.parametrize("version", [0, -1, 3, 99])
def test_a_version_the_contract_never_had_is_refused(
    rules: Rules, version: int
) -> None:
    # ADR 0003 point 3: an exporter could otherwise write a document for a version that never was.
    rules.define()

    assert rules.contract.version == 2
    with pytest.raises(ValueError, match="version"):
        rules.in_contract(version)


def test_the_rules_of_a_contract_that_doesnt_exist_are_refused(rules: Rules) -> None:
    # read_contract refuses the same id, and an empty list would say there's a contract with none.
    with pytest.raises(ValueError, match="no such contract"):
        rules_in_contract(rules.engine, "no-such-contract")


def test_rules_in_a_contract_say_what_they_are_about(rules: Rules) -> None:
    # ADR 0003 point 5: an export works from these.
    ref = rules.define()

    found = rules_in_contract(rules.engine, rules.contract_id)

    assert found == [read_item(rules.engine, ref)]
