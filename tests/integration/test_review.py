"""Trust-state transitions, CFX-APP-first-slice AC-35, AC-36, AC-37, AC-38, AC-40, AC-41 and AC-55."""

import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from artifacts.profiles import (
    MAX_OBSERVATIONS_PER_TRANSACTION,
    NewFinding,
    NewObservation,
    add_finding,
    add_observations,
    read_observations,
    start_profile_run,
)
from artifacts.provenance import NO_DETAILS, EventDetails
from artifacts.review import (
    EntryContent,
    EntrySubject,
    ItemNotFound,
    ItemRef,
    RuleSubject,
    accept,
    add_contract,
    create_entry,
    create_rule,
    defer,
    demote,
    edit,
    history,
    promote,
    read_item,
    reject,
    return_to_inbox,
    review_inbox,
)
from artifacts.sources import StoredField, StoredSource, add_csv_source, read_dataset
from artifacts.store import WorkspaceBusy
from artifacts.workspace import BUSY_TIMEOUT_MILLISECONDS, Workspace, open_workspace
from domain.trust import Action, Actor, ItemKind, Rejected, TrustState
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, IntegrityError

AI_DETAILS = EventDetails(
    ai_provider="ollama",
    ai_model="llama3",
    prompt_version="describe-v1",
    approved_context_categories=("column_names", "aggregates"),
    evidence_references=("profile-run-42/customer_id",),
)
FIRST = EntryContent(description="The customer", business_definition="Who bought it")
SECOND = EntryContent(description="The buyer", business_definition="Who paid")


@dataclass
class Catalogue:
    workspace: Workspace
    dataset_id: str
    field_id: str
    contract_id: str
    run: str = ""

    @property
    def engine(self):
        return self.workspace.engine

    def everything(self) -> list[str]:
        connection = sqlite3.connect(self.workspace.directory / "workspace.db")
        try:
            return list(connection.iterdump())
        finally:
            connection.close()

    def entry(self, state: TrustState = TrustState.INFERRED) -> ItemRef:
        actor = Actor.AI if state is TrustState.INFERRED else Actor.USER
        details = AI_DETAILS if actor is Actor.AI else NO_DETAILS
        return create_entry(
            self.engine, actor, self.dataset_id, FIRST, state, details=details
        )

    def another_dataset_field(self) -> str:
        add_csv_source(
            self.engine,
            StoredSource(
                id="source-2",
                path=Path("/data/orders.csv"),
                delimiter=",",
                encoding="UTF-8",
                has_header=True,
                size_bytes=10,
                modified_at="2024-01-01T00:00:00+00:00",
                fields=(StoredField("total", "BIGINT", "integer"),),
            ),
        )
        return read_dataset(self.engine, "source-2").fields[0].id

    def observation(self, count: int = 1) -> list[NewObservation]:
        return [
            NewObservation(
                self.dataset_id, None, "row_count", 12, "full", "count", "exact"
            )
        ] * count

    def run_with_observation(self) -> tuple[str, str]:
        run = start_profile_run(self.engine, scope={}, mode="full")
        add_observations(self.engine, Actor.SYSTEM, run, self.observation())
        return run, read_observations(self.engine, run)[0].id

    def finding(self, actor: Actor = Actor.SYSTEM, details=None) -> ItemRef:
        run, observation = self.run_with_observation()
        return ItemRef(
            ItemKind.FINDING,
            add_finding(
                self.engine,
                actor,
                NewFinding(run, (observation,), "A key?", "It joins", "Confirm it"),
                details or NO_DETAILS,
            ),
        )

    def rule(self, state: TrustState = TrustState.INFERRED) -> ItemRef:
        """A rule in this state, reached the way a real one gets there."""
        actor = Actor.AI if state is TrustState.INFERRED else Actor.USER
        first = TrustState.CONFIRMED if state is TrustState.DEFINED else state
        rule = create_rule(
            self.engine,
            actor,
            self.contract_id,
            "required",
            [("subject", self.field_id)],
            {},
            first,
            details=AI_DETAILS if actor is Actor.AI else NO_DETAILS,
        )
        if state is TrustState.DEFINED:
            promote(self.engine, Actor.USER, rule)
        return rule

    def item(self, kind: ItemKind, state: TrustState = TrustState.INFERRED) -> ItemRef:
        return (
            self.entry(state) if kind is ItemKind.DICTIONARY_ENTRY else self.rule(state)
        )


@pytest.fixture
def catalogue(tmp_path: Path) -> Iterator[Catalogue]:
    workspace = open_workspace(tmp_path / "workspace")
    add_csv_source(
        workspace.engine,
        StoredSource(
            id="source-1",
            path=Path("/data/customers.csv"),
            delimiter=",",
            encoding="UTF-8",
            has_header=True,
            size_bytes=10,
            modified_at="2024-01-01T00:00:00+00:00",
            fields=(StoredField("id", "BIGINT", "integer"),),
        ),
    )
    dataset = read_dataset(workspace.engine, "source-1")
    contract = add_contract(
        workspace.engine, dataset.id, purpose="Billing", limitations=""
    )
    yield Catalogue(workspace, dataset.id, dataset.fields[0].id, contract)
    workspace.close()


REVIEWABLE = [ItemKind.DICTIONARY_ENTRY, ItemKind.CONTRACT_RULE]


def test_a_dictionary_entry_reads_back(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-35: DictionaryEntry.
    content = EntryContent(
        description="The customer",
        business_definition="Who bought it",
        examples=("C-1", "C-2"),
        relationships=("orders.customer_id",),
        limitations="EU only",
        assumptions="One customer per account",
    )

    ref = create_entry(
        catalogue.engine,
        Actor.USER,
        catalogue.dataset_id,
        content,
        TrustState.CONFIRMED,
        field_id=catalogue.field_id,
    )

    item = read_item(catalogue.engine, ref)
    assert item.state is TrustState.CONFIRMED
    assert item.revision == 1
    assert item.content == content
    assert item.subject == EntrySubject(catalogue.dataset_id, catalogue.field_id)


def test_a_contract_rule_reads_back_with_its_fields(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-35: ContractRule, in the shape in ADR 0003 point 2.
    ref = create_rule(
        catalogue.engine,
        Actor.USER,
        catalogue.contract_id,
        "accepted_values",
        [("subject", catalogue.field_id)],
        {"values": ["red", "blue"]},
        TrustState.CONFIRMED,
    )

    item = read_item(catalogue.engine, ref)
    assert item.state is TrustState.CONFIRMED
    assert item.content == {"values": ["red", "blue"]}
    assert item.subject == RuleSubject(
        catalogue.contract_id,
        "accepted_values",
        (("subject", catalogue.field_id),),
    )


def test_a_provenance_event_holds_everything_section_12_lists(
    catalogue: Catalogue,
) -> None:
    # CFX-APP-first-slice/AC-35: ProvenanceEvent.
    ref = create_entry(
        catalogue.engine,
        Actor.AI,
        catalogue.dataset_id,
        FIRST,
        TrustState.INFERRED,
        details=AI_DETAILS,
    )

    event = history(catalogue.engine, ref)[0]

    assert event.author is Actor.AI
    assert event.occurred_at.startswith("20")
    assert event.action is Action.CREATE_INFERRED
    assert event.from_state is None
    assert event.to_state is TrustState.INFERRED
    assert event.previous_value is None
    assert event.ai_provider == "ollama"
    assert event.ai_model == "llama3"
    assert event.prompt_version == "describe-v1"
    assert event.approved_context_categories == ("column_names", "aggregates")
    assert event.evidence_references == ("profile-run-42/customer_id",)
    assert event.reviewer is None
    assert event.revision == 1


def test_recording_an_observation_records_its_event(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-36: New -> Observed.
    run = start_profile_run(catalogue.engine, scope={}, mode="full")

    add_observations(catalogue.engine, Actor.SYSTEM, run, catalogue.observation())

    observation = read_observations(catalogue.engine, run)[0]
    event = history(catalogue.engine, ItemRef(ItemKind.OBSERVATION, observation.id))[0]
    assert event.author is Actor.SYSTEM
    assert (event.from_state, event.to_state) == (None, TrustState.OBSERVED)


@pytest.mark.parametrize("kind", REVIEWABLE)
@pytest.mark.parametrize("state", [TrustState.INFERRED, TrustState.CONFIRMED])
def test_creating_an_item_records_its_event(
    catalogue: Catalogue, kind: ItemKind, state: TrustState
) -> None:
    # CFX-APP-first-slice/AC-36: New -> Inferred and New -> Confirmed.
    ref = catalogue.item(kind, state)

    assert read_item(catalogue.engine, ref).state is state
    event = history(catalogue.engine, ref)[0]
    assert (event.from_state, event.to_state) == (None, state)
    assert event.author is (Actor.AI if state is TrustState.INFERRED else Actor.USER)


@pytest.mark.parametrize("kind", REVIEWABLE)
def test_accepting_confirms_an_item_in_a_new_revision(
    catalogue: Catalogue, kind: ItemKind
) -> None:
    # CFX-APP-first-slice/AC-36: Inferred -> Confirmed, by Accept.
    ref = catalogue.item(kind)

    accept(catalogue.engine, Actor.USER, ref, EventDetails(reviewer="me"))

    item = read_item(catalogue.engine, ref)
    assert item.state is TrustState.CONFIRMED
    assert item.revision == 2
    event = history(catalogue.engine, ref)[-1]
    assert event.action is Action.ACCEPT
    assert (event.from_state, event.to_state) == (
        TrustState.INFERRED,
        TrustState.CONFIRMED,
    )
    assert event.author is Actor.USER
    assert event.reviewer == "me"
    assert event.revision == 2


def test_editing_an_inferred_item_confirms_it(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-36: Inferred -> Confirmed, by Edit.
    ref = catalogue.entry()

    edit(catalogue.engine, Actor.USER, ref, SECOND)

    item = read_item(catalogue.engine, ref)
    assert item.state is TrustState.CONFIRMED
    assert item.content == SECOND
    assert history(catalogue.engine, ref)[-1].action is Action.EDIT


@pytest.mark.parametrize(
    ("kind", "state", "content"),
    [
        (ItemKind.DICTIONARY_ENTRY, TrustState.CONFIRMED, SECOND),
        (ItemKind.CONTRACT_RULE, TrustState.CONFIRMED, {"note": "changed"}),
        (ItemKind.CONTRACT_RULE, TrustState.DEFINED, {"note": "changed"}),
    ],
)
def test_editing_keeps_the_state_and_the_earlier_revision(
    catalogue: Catalogue, kind: ItemKind, state: TrustState, content
) -> None:
    # CFX-APP-first-slice/AC-36: edits.
    ref = catalogue.item(kind, state)
    before = read_item(catalogue.engine, ref)

    edit(catalogue.engine, Actor.USER, ref, content)

    after = read_item(catalogue.engine, ref)
    assert after.state is state
    assert after.revision == before.revision + 1
    assert after.content == content
    event = history(catalogue.engine, ref)[-1]
    assert (event.from_state, event.to_state) == (state, state)
    assert event.previous_value is not None
    table = (
        "dictionary_entry_revision"
        if kind is ItemKind.DICTIONARY_ENTRY
        else "contract_rule_revision"
    )
    assert (
        sum(f'"{table}"' in line for line in catalogue.everything()) >= after.revision
    )


def test_promoting_makes_a_rule_defined(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-36: Confirmed -> Defined.
    ref = catalogue.rule(TrustState.CONFIRMED)

    promote(catalogue.engine, Actor.USER, ref)

    assert read_item(catalogue.engine, ref).state is TrustState.DEFINED
    event = history(catalogue.engine, ref)[-1]
    assert event.action is Action.PROMOTE
    assert (event.from_state, event.to_state) == (
        TrustState.CONFIRMED,
        TrustState.DEFINED,
    )


@pytest.mark.parametrize(
    ("kind", "start", "end"),
    [
        (ItemKind.DICTIONARY_ENTRY, TrustState.CONFIRMED, TrustState.INFERRED),
        (ItemKind.CONTRACT_RULE, TrustState.CONFIRMED, TrustState.INFERRED),
        (ItemKind.CONTRACT_RULE, TrustState.DEFINED, TrustState.CONFIRMED),
    ],
)
def test_both_demotions(
    catalogue: Catalogue, kind: ItemKind, start: TrustState, end: TrustState
) -> None:
    # CFX-APP-first-slice/AC-36: Confirmed -> Inferred and Defined -> Confirmed.
    ref = catalogue.item(kind, start)
    before = read_item(catalogue.engine, ref)

    demote(catalogue.engine, Actor.USER, ref)

    after = read_item(catalogue.engine, ref)
    assert after.state is end
    assert after.content == before.content
    event = history(catalogue.engine, ref)[-1]
    assert event.action is Action.DEMOTE
    assert (event.from_state, event.to_state) == (start, end)


ATTEMPTS = {
    "accept": lambda engine, actor, ref: accept(engine, actor, ref),
    "edit": lambda engine, actor, ref: edit(engine, actor, ref, SECOND),
    "promote": lambda engine, actor, ref: promote(engine, actor, ref),
    "demote": lambda engine, actor, ref: demote(engine, actor, ref),
    "reject": lambda engine, actor, ref: reject(engine, actor, ref),
    "defer": lambda engine, actor, ref: defer(engine, actor, ref),
}


@pytest.mark.parametrize("actor", [Actor.AI, Actor.SYSTEM])
@pytest.mark.parametrize("attempt", ATTEMPTS)
@pytest.mark.parametrize("kind", REVIEWABLE)
def test_ai_and_system_are_rejected_and_nothing_is_written(
    catalogue: Catalogue, actor: Actor, attempt: str, kind: ItemKind
) -> None:
    # CFX-APP-first-slice/AC-37
    ref = catalogue.item(kind)
    before = catalogue.everything()

    with pytest.raises(Rejected):
        ATTEMPTS[attempt](catalogue.engine, actor, ref)

    assert catalogue.everything() == before


@pytest.mark.parametrize("actor", [Actor.AI, Actor.SYSTEM])
@pytest.mark.parametrize("state", [TrustState.CONFIRMED, TrustState.DEFINED])
def test_ai_and_system_cannot_write_an_item_as_confirmed_or_defined(
    catalogue: Catalogue, actor: Actor, state: TrustState
) -> None:
    # CFX-APP-first-slice/AC-37: AI output always lands as Inferred.
    before = catalogue.everything()

    with pytest.raises(Rejected):
        create_entry(catalogue.engine, actor, catalogue.dataset_id, FIRST, state)
    with pytest.raises(Rejected):
        create_rule(
            catalogue.engine,
            actor,
            catalogue.contract_id,
            "required",
            [("subject", catalogue.field_id)],
            {},
            state,
        )

    assert catalogue.everything() == before


def test_ai_cannot_record_an_observation(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-37: only the profiling engine observes.
    run = start_profile_run(catalogue.engine, scope={}, mode="full")
    before = catalogue.everything()

    with pytest.raises(Rejected):
        add_observations(catalogue.engine, Actor.AI, run, catalogue.observation())

    assert catalogue.everything() == before


def test_a_user_cannot_skip_a_step_and_nothing_is_written(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-36: Inferred -> Defined isn't in the table for anyone.
    inferred = catalogue.rule()
    confirmed_entry = catalogue.entry(TrustState.CONFIRMED)
    before = catalogue.everything()

    with pytest.raises(Rejected):
        promote(catalogue.engine, Actor.USER, inferred)
    with pytest.raises(Rejected):
        promote(catalogue.engine, Actor.USER, confirmed_entry)
    with pytest.raises(Rejected):
        accept(catalogue.engine, Actor.USER, confirmed_entry)

    assert catalogue.everything() == before


def test_an_unknown_item_is_not_found(catalogue: Catalogue) -> None:
    ref = ItemRef(ItemKind.DICTIONARY_ENTRY, "nothing")

    with pytest.raises(ItemNotFound):
        read_item(catalogue.engine, ref)
    with pytest.raises(ItemNotFound):
        accept(catalogue.engine, Actor.USER, ref)


@pytest.mark.parametrize("kind", REVIEWABLE)
def test_an_inferred_item_is_in_the_review_inbox(
    catalogue: Catalogue, kind: ItemKind
) -> None:
    # CFX-APP-first-slice/AC-38
    ref = catalogue.item(kind)
    catalogue.item(kind, TrustState.CONFIRMED)

    assert review_inbox(catalogue.engine) == [ref]


@pytest.mark.parametrize("kind", REVIEWABLE)
@pytest.mark.parametrize(
    ("act", "action"), [(reject, Action.REJECT), (defer, Action.DEFER)]
)
def test_rejecting_or_deferring_keeps_it_inferred_and_leaves_the_inbox(
    catalogue: Catalogue, kind: ItemKind, act, action: Action
) -> None:
    # CFX-APP-first-slice/AC-38
    ref = catalogue.item(kind)

    act(catalogue.engine, Actor.USER, ref)

    item = read_item(catalogue.engine, ref)
    assert item.state is TrustState.INFERRED
    assert item.revision == 1
    assert review_inbox(catalogue.engine) == []
    event = history(catalogue.engine, ref)[-1]
    assert event.action is action
    assert (event.from_state, event.to_state) == (
        TrustState.INFERRED,
        TrustState.INFERRED,
    )


def test_a_deferred_item_can_return_to_the_inbox(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-38
    ref = catalogue.entry()
    defer(catalogue.engine, Actor.USER, ref)

    return_to_inbox(catalogue.engine, Actor.USER, ref)

    assert review_inbox(catalogue.engine) == [ref]
    assert [e.action for e in history(catalogue.engine, ref)] == [
        Action.CREATE_INFERRED,
        Action.DEFER,
        Action.RETURN_TO_INBOX,
    ]


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([], id="never deferred"),
        pytest.param([reject], id="rejected"),
        pytest.param([defer, return_to_inbox], id="already returned"),
    ],
)
def test_only_a_deferred_item_can_return(catalogue: Catalogue, steps: list) -> None:
    # CFX-APP-first-slice/AC-38: Section 12 only says Deferred items return.
    ref = catalogue.entry()
    for step in steps:
        step(catalogue.engine, Actor.USER, ref)
    before = catalogue.everything()

    with pytest.raises(Rejected):
        return_to_inbox(catalogue.engine, Actor.USER, ref)

    assert catalogue.everything() == before


@pytest.mark.parametrize("first", [reject, defer])
@pytest.mark.parametrize("second", [reject, defer])
def test_an_item_that_left_the_inbox_cannot_leave_it_again(
    catalogue: Catalogue, first, second
) -> None:
    # CFX-APP-first-slice/AC-38: a second outcome would misstate what the user decided.
    ref = catalogue.entry()
    first(catalogue.engine, Actor.USER, ref)
    before = catalogue.everything()

    with pytest.raises(Rejected):
        second(catalogue.engine, Actor.USER, ref)

    assert catalogue.everything() == before


@pytest.mark.parametrize("left_by", [reject, defer])
def test_an_item_that_left_the_inbox_can_still_be_accepted(
    catalogue: Catalogue, left_by
) -> None:
    # CFX-APP-first-slice/AC-38: it stays Inferred, and Inferred -> Confirmed is always a user's call.
    ref = catalogue.entry()
    left_by(catalogue.engine, Actor.USER, ref)

    accept(catalogue.engine, Actor.USER, ref)

    assert read_item(catalogue.engine, ref).state is TrustState.CONFIRMED


def test_a_demoted_item_returns_to_the_inbox(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-38: it is Inferred again, and no outcome has been decided for it.
    ref = catalogue.entry(TrustState.CONFIRMED)

    demote(catalogue.engine, Actor.USER, ref)

    assert review_inbox(catalogue.engine) == [ref]


OPERATIONS = {
    "accept": lambda catalogue, ref: accept(catalogue.engine, Actor.USER, ref),
    "edit": lambda catalogue, ref: edit(catalogue.engine, Actor.USER, ref, SECOND),
    "reject": lambda catalogue, ref: reject(catalogue.engine, Actor.USER, ref),
    "defer": lambda catalogue, ref: defer(catalogue.engine, Actor.USER, ref),
    "create entry": lambda catalogue, ref: create_entry(
        catalogue.engine, Actor.USER, catalogue.dataset_id, FIRST, TrustState.CONFIRMED
    ),
    "create rule": lambda catalogue, ref: create_rule(
        catalogue.engine,
        Actor.USER,
        catalogue.contract_id,
        "required",
        [("subject", catalogue.field_id)],
        {},
        TrustState.CONFIRMED,
    ),
    "add finding": lambda catalogue, ref: add_finding(
        catalogue.engine,
        Actor.SYSTEM,
        NewFinding(catalogue.run, (), "A key?", "It joins", "Confirm it"),
    ),
}


def fail_when_writing_an_event(catalogue: Catalogue) -> None:
    """A real failure at the last step: the revision has been written, and now its event can't be."""
    with catalogue.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TRIGGER forced_failure BEFORE INSERT ON provenance_event
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
                """
            )
        )


def stop_failing(catalogue: Catalogue) -> None:
    with catalogue.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER forced_failure"))


@pytest.mark.parametrize("operation", OPERATIONS)
def test_a_failure_halfway_saves_neither_the_change_nor_its_event(
    catalogue: Catalogue, operation: str
) -> None:
    # CFX-APP-first-slice/AC-40
    ref = catalogue.entry()
    catalogue.run = start_profile_run(catalogue.engine, scope={}, mode="full")
    fail_when_writing_an_event(catalogue)
    before = catalogue.everything()

    with pytest.raises(DBAPIError, match="forced failure"):
        OPERATIONS[operation](catalogue, ref)

    assert catalogue.everything() == before


@pytest.mark.parametrize(
    ("start", "act"),
    [
        (TrustState.CONFIRMED, promote),
        (TrustState.CONFIRMED, demote),
        (TrustState.DEFINED, demote),
    ],
)
def test_a_failure_halfway_leaves_a_rule_as_it_was(
    catalogue: Catalogue, start: TrustState, act
) -> None:
    # CFX-APP-first-slice/AC-40
    ref = catalogue.rule(start)
    fail_when_writing_an_event(catalogue)
    before = catalogue.everything()

    with pytest.raises(DBAPIError, match="forced failure"):
        act(catalogue.engine, Actor.USER, ref)

    assert catalogue.everything() == before
    assert read_item(catalogue.engine, ref).state is start


def test_a_failure_halfway_saves_no_observation(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-40: all of a batch or none of it.
    run = start_profile_run(catalogue.engine, scope={}, mode="full")
    fail_when_writing_an_event(catalogue)
    before = catalogue.everything()

    with pytest.raises(DBAPIError, match="forced failure"):
        add_observations(catalogue.engine, Actor.SYSTEM, run, catalogue.observation(3))

    assert catalogue.everything() == before


def test_the_workspace_keeps_working_after_a_failure_halfway(
    catalogue: Catalogue,
) -> None:
    # CFX-APP-first-slice/AC-40: the write lock must not stay held.
    ref = catalogue.entry()
    fail_when_writing_an_event(catalogue)
    with pytest.raises(DBAPIError, match="forced failure"):
        accept(catalogue.engine, Actor.USER, ref)
    stop_failing(catalogue)

    accept(catalogue.engine, Actor.USER, ref)

    assert read_item(catalogue.engine, ref).state is TrustState.CONFIRMED
    assert [e.action for e in history(catalogue.engine, ref)] == [
        Action.CREATE_INFERRED,
        Action.ACCEPT,
    ]


# An unexpected error stays uncaught in its thread, which then leaves a result missing.
EXPECTED_ERRORS = (Rejected, DBAPIError, WorkspaceBusy)


def run_at_once(count: int, job) -> list[Exception | None]:
    barrier = threading.Barrier(count)
    results: list[Exception | None] = []

    def worker() -> None:
        barrier.wait()
        try:
            job()
            results.append(None)
        except EXPECTED_ERRORS as error:
            results.append(error)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


@pytest.mark.parametrize("attempt", range(5))
def test_of_many_accepts_at_once_one_succeeds_and_the_rest_are_rejected(
    catalogue: Catalogue, attempt: int
) -> None:
    # CFX-APP-first-slice/AC-41: repeated, because a race only shows up some of the time.
    ref = catalogue.entry()

    results = run_at_once(8, lambda: accept(catalogue.engine, Actor.USER, ref))

    assert len(results) == 8
    assert results.count(None) == 1
    assert all(isinstance(result, Rejected) for result in results if result is not None)
    assert read_item(catalogue.engine, ref).revision == 2
    actions = [e.action for e in history(catalogue.engine, ref)]
    assert actions == [Action.CREATE_INFERRED, Action.ACCEPT]


def test_accepts_on_different_items_at_once_all_succeed(
    catalogue: Catalogue,
) -> None:
    # CFX-APP-first-slice/AC-41: each one reads, then writes, which is what makes a deferred
    # transaction get SQLITE_BUSY.
    refs = [catalogue.entry() for _ in range(40)]

    errors: list[Exception] = []

    def worker(chunk: list[ItemRef]) -> None:
        for ref in chunk:
            try:
                accept(catalogue.engine, Actor.USER, ref)
            except EXPECTED_ERRORS as error:
                errors.append(error)

    threads = [threading.Thread(target=worker, args=(refs[i::4],)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert all(read_item(catalogue.engine, ref).revision == 2 for ref in refs)


def test_a_review_finishes_within_busy_timeout_during_a_profile_run(
    catalogue: Catalogue,
) -> None:
    # CFX-APP-first-slice/AC-41
    run = start_profile_run(catalogue.engine, scope={}, mode="full")
    refs = [catalogue.entry() for _ in range(15)]
    stop = threading.Event()
    writer_errors: list[Exception] = []

    def write_observations() -> None:
        while not stop.is_set():
            try:
                add_observations(
                    catalogue.engine, Actor.SYSTEM, run, catalogue.observation(50)
                )
            except EXPECTED_ERRORS as error:
                writer_errors.append(error)
                return

    writer = threading.Thread(target=write_observations)
    writer.start()
    try:
        while not read_observations(catalogue.engine, run):
            assert writer.is_alive(), "the writer stopped before writing anything"
            time.sleep(0.01)
        at_start = len(read_observations(catalogue.engine, run))
        durations = []
        for ref in refs:
            started = time.monotonic()
            accept(catalogue.engine, Actor.USER, ref)
            durations.append(time.monotonic() - started)
        at_end = len(read_observations(catalogue.engine, run))
    finally:
        stop.set()
        writer.join()

    assert writer_errors == []
    assert at_end > at_start, (
        "no observations were written meanwhile, so this proves nothing"
    )
    assert max(durations) < BUSY_TIMEOUT_MILLISECONDS / 1000
    assert all(
        read_item(catalogue.engine, ref).state is TrustState.CONFIRMED for ref in refs
    )


def test_a_contract_for_an_unknown_dataset_is_not_called_a_duplicate(
    catalogue: Catalogue,
) -> None:
    # Found writing T07: the duplicate check caught every integrity error, not only its own.
    with pytest.raises(IntegrityError):
        add_contract(catalogue.engine, "nothing", purpose="", limitations="")


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(
            lambda c: edit(c.engine, Actor.USER, c.entry(), {"not": "an entry"}),
            id="edit an entry with params",
        ),
        pytest.param(
            lambda c: edit(c.engine, Actor.USER, c.rule(), FIRST),
            id="edit a rule with an entry",
        ),
        pytest.param(
            lambda c: create_entry(
                c.engine, Actor.USER, c.dataset_id, {"x": 1}, TrustState.CONFIRMED
            ),
            id="create an entry from params",
        ),
        pytest.param(
            lambda c: create_rule(
                c.engine,
                Actor.USER,
                c.contract_id,
                "required",
                [("subject", c.field_id)],
                FIRST,
                TrustState.CONFIRMED,
            ),
            id="create a rule from an entry",
        ),
    ],
)
def test_the_wrong_kind_of_content_is_refused(catalogue: Catalogue, write) -> None:
    # Found reviewing T07: it committed, and then the entry could never be read again.
    with pytest.raises(TypeError):
        write(catalogue)

    with catalogue.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT count(*) FROM provenance_event WHERE action = 'edit'")
        ).scalar()
    assert rows == 0
    for ref in review_inbox(catalogue.engine):
        read_item(catalogue.engine, ref)


@pytest.mark.parametrize("attempt", ATTEMPTS)
@pytest.mark.parametrize("kind", [ItemKind.OBSERVATION, ItemKind.FINDING])
def test_what_is_written_once_cannot_be_reviewed(
    catalogue: Catalogue, attempt: str, kind: ItemKind
) -> None:
    # CFX-APP-first-slice/AC-37: refused like anything else the rules don't allow.
    ref = (
        catalogue.finding()
        if kind is ItemKind.FINDING
        else ItemRef(kind, catalogue.run_with_observation()[1])
    )
    before = catalogue.everything()

    with pytest.raises(Rejected):
        ATTEMPTS[attempt](catalogue.engine, Actor.USER, ref)

    assert catalogue.everything() == before
    with pytest.raises(ItemNotFound):
        read_item(catalogue.engine, ref)


def test_an_entry_cannot_use_a_field_from_another_dataset(catalogue: Catalogue) -> None:
    # Found reviewing T07.
    other_field = catalogue.another_dataset_field()
    before = catalogue.everything()

    with pytest.raises(IntegrityError):
        create_entry(
            catalogue.engine,
            Actor.USER,
            catalogue.dataset_id,
            FIRST,
            TrustState.CONFIRMED,
            field_id=other_field,
        )

    assert catalogue.everything() == before


def test_an_observation_cannot_use_a_field_from_another_dataset(
    catalogue: Catalogue,
) -> None:
    # Found reviewing T07.
    other_field = catalogue.another_dataset_field()
    run = start_profile_run(catalogue.engine, scope={}, mode="full")
    before = catalogue.everything()

    with pytest.raises(IntegrityError):
        add_observations(
            catalogue.engine,
            Actor.SYSTEM,
            run,
            [
                NewObservation(
                    catalogue.dataset_id,
                    other_field,
                    "uniqueness",
                    1,
                    "full",
                    "count",
                    "exact",
                )
            ],
        )

    assert catalogue.everything() == before


@pytest.mark.parametrize("missing", ["ai_provider", "ai_model", "prompt_version"])
def test_an_ai_event_must_say_where_it_came_from(
    catalogue: Catalogue, missing: str
) -> None:
    # Section 12: an AI item's provenance holds its provider, model and prompt version.
    details = replace(AI_DETAILS, **{missing: None})
    before = catalogue.everything()

    with pytest.raises(Rejected):
        create_entry(
            catalogue.engine,
            Actor.AI,
            catalogue.dataset_id,
            FIRST,
            TrustState.INFERRED,
            details=details,
        )

    assert catalogue.everything() == before


def test_a_finding_records_who_wrote_it_as_inferred(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-36: Section 5 labels a finding's interpretation as Inferred.
    ref = catalogue.finding(Actor.AI, AI_DETAILS)

    event = history(catalogue.engine, ref)[0]

    assert event.author is Actor.AI
    assert event.ai_model == "llama3"
    assert event.action is Action.CREATE_INFERRED
    assert (event.from_state, event.to_state) == (None, TrustState.INFERRED)
    assert history(catalogue.engine, catalogue.finding())[0].author is Actor.SYSTEM


def test_a_user_cannot_write_a_finding(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-37: a finding comes from a heuristic or AI.
    run = start_profile_run(catalogue.engine, scope={}, mode="full")
    before = catalogue.everything()

    with pytest.raises(Rejected):
        add_finding(
            catalogue.engine,
            Actor.USER,
            NewFinding(run, (), "A key?", "It joins", "Confirm it"),
        )

    assert catalogue.everything() == before


def entry_for_field(catalogue: Catalogue, state: TrustState) -> ItemRef:
    actor = Actor.AI if state is TrustState.INFERRED else Actor.USER
    return create_entry(
        catalogue.engine,
        actor,
        catalogue.dataset_id,
        FIRST,
        state,
        field_id=catalogue.field_id,
        details=AI_DETAILS if actor is Actor.AI else NO_DETAILS,
    )


@pytest.mark.parametrize(
    "second",
    [
        pytest.param(
            lambda c: entry_for_field(c, TrustState.CONFIRMED), id="written directly"
        ),
        pytest.param(
            lambda c: accept(
                c.engine, Actor.USER, entry_for_field(c, TrustState.INFERRED)
            ),
            id="accepted",
        ),
        pytest.param(
            lambda c: edit(
                c.engine, Actor.USER, entry_for_field(c, TrustState.INFERRED), SECOND
            ),
            id="edited",
        ),
    ],
)
def test_a_field_has_at_most_one_confirmed_entry(catalogue: Catalogue, second) -> None:
    # CFX-APP-first-slice/AC-55
    entry_for_field(catalogue, TrustState.CONFIRMED)

    with pytest.raises(Rejected, match="already has a Confirmed"):
        second(catalogue)

    with catalogue.engine.connect() as connection:
        confirmed = connection.execute(
            text(
                "SELECT count(*) FROM dictionary_entry_revision WHERE state = 'confirmed'"
            )
        ).scalar()
    assert confirmed == 1


def test_a_refused_second_entry_writes_nothing(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-55
    entry_for_field(catalogue, TrustState.CONFIRMED)
    candidate = entry_for_field(catalogue, TrustState.INFERRED)
    before = catalogue.everything()

    with pytest.raises(Rejected):
        accept(catalogue.engine, Actor.USER, candidate)
    with pytest.raises(Rejected):
        entry_for_field(catalogue, TrustState.CONFIRMED)

    assert catalogue.everything() == before


def test_candidates_are_allowed_and_demoting_frees_the_field(
    catalogue: Catalogue,
) -> None:
    # CFX-APP-first-slice/AC-55
    first = entry_for_field(catalogue, TrustState.CONFIRMED)
    candidate = entry_for_field(catalogue, TrustState.INFERRED)
    assert review_inbox(catalogue.engine) == [candidate]

    demote(catalogue.engine, Actor.USER, first)
    accept(catalogue.engine, Actor.USER, candidate)

    assert read_item(catalogue.engine, candidate).state is TrustState.CONFIRMED


def test_editing_the_confirmed_entry_is_not_a_second_one(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-55
    ref = entry_for_field(catalogue, TrustState.CONFIRMED)

    edit(catalogue.engine, Actor.USER, ref, SECOND)

    assert read_item(catalogue.engine, ref).content == SECOND


def test_a_different_field_can_have_its_own_confirmed_entry(
    catalogue: Catalogue,
) -> None:
    # CFX-APP-first-slice/AC-55
    entry_for_field(catalogue, TrustState.CONFIRMED)
    other_field = catalogue.another_dataset_field()

    other = create_entry(
        catalogue.engine,
        Actor.USER,
        read_dataset(catalogue.engine, "source-2").id,
        FIRST,
        TrustState.CONFIRMED,
        field_id=other_field,
    )

    assert read_item(catalogue.engine, other).state is TrustState.CONFIRMED


def test_entries_for_a_whole_dataset_are_not_limited(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-55 is about a field, and these aren't about one.
    entry_for_field(catalogue, TrustState.CONFIRMED)

    catalogue.entry(TrustState.CONFIRMED)
    catalogue.entry(TrustState.CONFIRMED)


def test_two_entries_confirmed_at_once_cannot_both_win(catalogue: Catalogue) -> None:
    # CFX-APP-first-slice/AC-55: the check and the write share one write lock.
    candidates = [entry_for_field(catalogue, TrustState.INFERRED) for _ in range(8)]
    remaining = iter(candidates)

    results = run_at_once(
        8, lambda: accept(catalogue.engine, Actor.USER, next(remaining))
    )

    assert results.count(None) == 1
    assert all(isinstance(result, Rejected) for result in results if result is not None)


@pytest.mark.parametrize(
    "column_values",
    [
        pytest.param(("NULL", "NULL"), id="no target"),
        pytest.param(("{observation}", "{revision}"), id="two targets"),
    ],
)
def test_an_event_has_exactly_one_target(
    catalogue: Catalogue, column_values: tuple[str, str]
) -> None:
    # The reviewer's mutation survived: nothing pinned this check.
    entry = catalogue.entry()
    _, observation = catalogue.run_with_observation()
    with catalogue.engine.connect() as connection:
        revision = connection.execute(
            text("SELECT id FROM dictionary_entry_revision WHERE entry_id = :id"),
            {"id": entry.id},
        ).scalar()
    values = [
        value.format(observation=f"'{observation}'", revision=f"'{revision}'")
        for value in column_values
    ]

    connection = sqlite3.connect(catalogue.workspace.directory / "workspace.db")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(
                f"""
                INSERT INTO provenance_event (
                    id, author, occurred_at, action, to_state, approved_context_categories,
                    evidence_references, observation_id, dictionary_entry_revision_id
                ) VALUES ('x', 'user', 't', 'edit', 'inferred', '[]', '[]', {values[0]}, {values[1]})
                """
            )
    finally:
        connection.close()


def test_review_queries_use_an_index_on_provenance(catalogue: Catalogue) -> None:
    # Found reviewing T07: every observation adds an event, and these scanned them all for each item,
    # so the inbox took 5.75 s at 100,000 observations. Reject and defer did it holding the write lock.
    ref = catalogue.entry()
    finding = catalogue.finding()
    _, observation = catalogue.run_with_observation()
    statements: list[tuple[str, object]] = []

    @event.listens_for(catalogue.engine, "before_cursor_execute")
    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().startswith("SELECT") and "provenance_event" in statement:
            statements.append((statement, parameters))

    review_inbox(catalogue.engine)
    defer(catalogue.engine, Actor.USER, ref)
    history(catalogue.engine, ref)
    history(catalogue.engine, finding)
    history(catalogue.engine, ItemRef(ItemKind.OBSERVATION, observation))
    event.remove(catalogue.engine, "before_cursor_execute", record)

    assert statements
    with catalogue.engine.connect() as connection:
        for statement, parameters in statements:
            plan = connection.exec_driver_sql(
                f"EXPLAIN QUERY PLAN {statement}", parameters
            ).all()
            assert not any("SCAN provenance_event" in row[3] for row in plan), statement


def test_observations_are_gathered_before_the_write_lock(catalogue: Catalogue) -> None:
    # Found reviewing T07: a slow generator held the lock, and a review got WorkspaceBusy. If this
    # one still ran under the lock, the write inside it would time out waiting for itself.
    run = start_profile_run(catalogue.engine, scope={}, mode="full")
    ref = catalogue.entry()

    def slow_source() -> Iterator[NewObservation]:
        accept(catalogue.engine, Actor.USER, ref)
        yield from catalogue.observation()

    add_observations(catalogue.engine, Actor.SYSTEM, run, slow_source())

    assert read_item(catalogue.engine, ref).state is TrustState.CONFIRMED
    assert len(read_observations(catalogue.engine, run)) == 1


def test_one_transaction_holds_a_limited_number_of_observations(
    catalogue: Catalogue,
) -> None:
    # CFX-APP-first-slice/AC-41: 40,000 in one transaction made a review time out at 5 s. 1,000 took
    # 0.5 s on a workspace already holding many rows. A caller with more commits them in batches.
    run = start_profile_run(catalogue.engine, scope={}, mode="full")
    largest = catalogue.observation(MAX_OBSERVATIONS_PER_TRANSACTION)

    add_observations(catalogue.engine, Actor.SYSTEM, run, largest)

    before = catalogue.everything()
    with pytest.raises(ValueError, match="at most"):
        add_observations(
            catalogue.engine,
            Actor.SYSTEM,
            run,
            catalogue.observation(MAX_OBSERVATIONS_PER_TRANSACTION + 1),
        )
    assert catalogue.everything() == before


def test_a_dataset_level_observation_says_which_dataset(catalogue: Catalogue) -> None:
    # Found reviewing T07: one run can cover many tables, and two row counts looked the same.
    other_dataset = (
        catalogue.another_dataset_field(),
        read_dataset(catalogue.engine, "source-2").id,
    )[1]
    run = start_profile_run(catalogue.engine, scope={}, mode="full")

    add_observations(
        catalogue.engine,
        Actor.SYSTEM,
        run,
        [
            *catalogue.observation(),
            NewObservation(
                other_dataset, None, "row_count", 7, "full", "count", "exact"
            ),
        ],
    )

    assert [
        (o.dataset_id, o.value) for o in read_observations(catalogue.engine, run)
    ] == [(catalogue.dataset_id, 12), (other_dataset, 7)]


@pytest.mark.parametrize(
    ("failing_table", "write", "value"),
    [
        (
            "dictionary_entry_revision",
            lambda c: create_entry(
                c.engine,
                Actor.USER,
                c.dataset_id,
                EntryContent("d", "b", examples=("555-12-3456",)),
                TrustState.CONFIRMED,
            ),
            "555-12-3456",
        ),
        (
            "dataset",
            lambda c: add_csv_source(
                c.engine,
                StoredSource(
                    id="source-3",
                    path=Path("/home/someone/jane_doe_salaries.csv"),
                    delimiter=",",
                    encoding="UTF-8",
                    has_header=True,
                    size_bytes=1,
                    modified_at="t",
                    fields=(),
                ),
            ),
            "jane_doe_salaries",
        ),
    ],
)
def test_a_failed_write_keeps_its_values_out_of_the_error(
    catalogue: Catalogue, failing_table: str, write, value: str
) -> None:
    # Section 7: uvicorn logs this text, and these values can be personal data.
    with catalogue.engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TRIGGER forced_failure BEFORE INSERT ON {failing_table}
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
                """
            )
        )

    with pytest.raises(DBAPIError, match="forced failure") as failure:
        write(catalogue)

    assert value not in str(failure.value)
