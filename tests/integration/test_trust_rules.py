"""The trust-state rules in Section 12 of the direction doc, as amended by ADR 0003.

CFX-APP-first-slice AC-36, AC-37 and AC-38. The table below is written out from Section 12 by hand,
so it can't agree with the code just because it was worked out the same way.
"""

import itertools

import pytest
from domain.trust import Action, Actor, ItemKind, Rejected, TrustState, next_state

CONFIRMED = TrustState.CONFIRMED
DEFINED = TrustState.DEFINED
INFERRED = TrustState.INFERRED
OBSERVED = TrustState.OBSERVED
AI = Actor.AI
SYSTEM = Actor.SYSTEM
USER = Actor.USER
ENTRY = ItemKind.DICTIONARY_ENTRY
RULE = ItemKind.CONTRACT_RULE
FINDING = ItemKind.FINDING
OBSERVATION = ItemKind.OBSERVATION
NEW = None

ALLOWED = {
    # New -> Observed: the profiling engine, for an observation.
    (SYSTEM, Action.RECORD_OBSERVED, OBSERVATION, NEW): OBSERVED,
    # New -> Inferred: a heuristic or AI.
    (SYSTEM, Action.CREATE_INFERRED, ENTRY, NEW): INFERRED,
    (SYSTEM, Action.CREATE_INFERRED, RULE, NEW): INFERRED,
    (AI, Action.CREATE_INFERRED, ENTRY, NEW): INFERRED,
    (AI, Action.CREATE_INFERRED, RULE, NEW): INFERRED,
    # Section 5 labels a finding's interpretation as Inferred. Nothing reviews one further.
    (SYSTEM, Action.CREATE_INFERRED, FINDING, NEW): INFERRED,
    (AI, Action.CREATE_INFERRED, FINDING, NEW): INFERRED,
    # New -> Confirmed: a user writing it directly.
    (USER, Action.CREATE_CONFIRMED, ENTRY, NEW): CONFIRMED,
    (USER, Action.CREATE_CONFIRMED, RULE, NEW): CONFIRMED,
    # Inferred -> Confirmed: a user, by Accept or Edit.
    (USER, Action.ACCEPT, ENTRY, INFERRED): CONFIRMED,
    (USER, Action.ACCEPT, RULE, INFERRED): CONFIRMED,
    (USER, Action.EDIT, ENTRY, INFERRED): CONFIRMED,
    (USER, Action.EDIT, RULE, INFERRED): CONFIRMED,
    # Confirmed -> Defined: a user, and only a rule can be part of a contract.
    (USER, Action.PROMOTE, RULE, CONFIRMED): DEFINED,
    # Editing leaves the state as it was.
    (USER, Action.EDIT, ENTRY, CONFIRMED): CONFIRMED,
    (USER, Action.EDIT, RULE, CONFIRMED): CONFIRMED,
    (USER, Action.EDIT, RULE, DEFINED): DEFINED,
    # Demotions.
    (USER, Action.DEMOTE, ENTRY, CONFIRMED): INFERRED,
    (USER, Action.DEMOTE, RULE, CONFIRMED): INFERRED,
    (USER, Action.DEMOTE, RULE, DEFINED): CONFIRMED,
    # Reject and Defer: the item stays Inferred.
    (USER, Action.REJECT, ENTRY, INFERRED): INFERRED,
    (USER, Action.REJECT, RULE, INFERRED): INFERRED,
    (USER, Action.DEFER, ENTRY, INFERRED): INFERRED,
    (USER, Action.DEFER, RULE, INFERRED): INFERRED,
    (USER, Action.RETURN_TO_INBOX, ENTRY, INFERRED): INFERRED,
    (USER, Action.RETURN_TO_INBOX, RULE, INFERRED): INFERRED,
    # A newer run contradicts the evidence: the system flags it, and the state stays (Section 12).
    (SYSTEM, Action.FLAG_RE_REVIEW, ENTRY, INFERRED): INFERRED,
    (SYSTEM, Action.FLAG_RE_REVIEW, ENTRY, CONFIRMED): CONFIRMED,
    (SYSTEM, Action.FLAG_RE_REVIEW, RULE, INFERRED): INFERRED,
    (SYSTEM, Action.FLAG_RE_REVIEW, RULE, CONFIRMED): CONFIRMED,
    (SYSTEM, Action.FLAG_RE_REVIEW, RULE, DEFINED): DEFINED,
    # Keep as is: a user still agrees. An Inferred item has Accept for that (spec revision 9).
    (USER, Action.KEEP_AS_IS, ENTRY, CONFIRMED): CONFIRMED,
    (USER, Action.KEEP_AS_IS, RULE, CONFIRMED): CONFIRMED,
    (USER, Action.KEEP_AS_IS, RULE, DEFINED): DEFINED,
    # Deleting a rule writes a tombstone, which keeps its state (AC-45, ADR 0002 point 4).
    (USER, Action.DELETE, RULE, INFERRED): INFERRED,
    (USER, Action.DELETE, RULE, CONFIRMED): CONFIRMED,
    (USER, Action.DELETE, RULE, DEFINED): DEFINED,
}


@pytest.mark.parametrize(("key", "result"), ALLOWED.items(), ids=str)
def test_each_allowed_transition_gives_its_state(
    key: tuple, result: TrustState
) -> None:
    # CFX-APP-first-slice/AC-36
    actor, action, kind, state = key

    assert next_state(actor, action, kind, state) is result


EVERY_CASE = itertools.product(Actor, Action, ItemKind, [NEW, *TrustState])


@pytest.mark.parametrize(
    "key", [case for case in EVERY_CASE if case not in ALLOWED], ids=str
)
def test_everything_else_is_rejected(key: tuple) -> None:
    # CFX-APP-first-slice/AC-37, including AI or system Inferred -> Confirmed or Defined.
    actor, action, kind, state = key

    with pytest.raises(Rejected):
        next_state(actor, action, kind, state)


@pytest.mark.parametrize("actor", [AI, SYSTEM])
@pytest.mark.parametrize("action", [Action.ACCEPT, Action.EDIT, Action.PROMOTE])
def test_the_hard_rule_only_a_person_can_confirm_or_define(
    actor: Actor, action: Action
) -> None:
    # CFX-APP-first-slice/AC-37: named on its own because Section 3 calls it the hard rule.
    with pytest.raises(Rejected):
        next_state(actor, action, RULE, INFERRED)
