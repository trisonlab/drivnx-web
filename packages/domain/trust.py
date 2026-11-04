"""Who may do what to a trust state (Section 12 of the direction doc, as amended by ADR 0003)."""

from dataclasses import dataclass
from enum import StrEnum


class TrustState(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"
    DEFINED = "defined"


class Actor(StrEnum):
    SYSTEM = "system"
    AI = "ai"
    USER = "user"


class ItemKind(StrEnum):
    OBSERVATION = "observation"
    DICTIONARY_ENTRY = "dictionary_entry"
    CONTRACT_RULE = "contract_rule"
    FINDING = "finding"


class Action(StrEnum):
    RECORD_OBSERVED = "record_observed"
    CREATE_INFERRED = "create_inferred"
    CREATE_CONFIRMED = "create_confirmed"
    ACCEPT = "accept"
    EDIT = "edit"
    PROMOTE = "promote"
    DEMOTE = "demote"
    REJECT = "reject"
    DEFER = "defer"
    RETURN_TO_INBOX = "return_to_inbox"
    FLAG_RE_REVIEW = "flag_re_review"
    KEEP_AS_IS = "keep_as_is"


class Rejected(Exception):
    """The trust rules don't allow this."""


@dataclass(frozen=True)
class _Rule:
    actors: frozenset[Actor]
    kinds: frozenset[ItemKind]
    result: TrustState


_USER = frozenset({Actor.USER})
_SYSTEM = frozenset({Actor.SYSTEM})
_REVIEWABLE = frozenset({ItemKind.DICTIONARY_ENTRY, ItemKind.CONTRACT_RULE})
_ONLY_RULES = frozenset({ItemKind.CONTRACT_RULE})
_CONFIRMED = TrustState.CONFIRMED
_DEFINED = TrustState.DEFINED
_INFERRED = TrustState.INFERRED

_RULES: dict[tuple[Action, TrustState | None], _Rule] = {
    (Action.RECORD_OBSERVED, None): _Rule(
        _SYSTEM,
        frozenset({ItemKind.OBSERVATION}),
        TrustState.OBSERVED,
    ),
    (Action.CREATE_INFERRED, None): _Rule(
        frozenset({Actor.SYSTEM, Actor.AI}),
        _REVIEWABLE | {ItemKind.FINDING},
        _INFERRED,
    ),
    (Action.CREATE_CONFIRMED, None): _Rule(_USER, _REVIEWABLE, _CONFIRMED),
    (Action.ACCEPT, _INFERRED): _Rule(_USER, _REVIEWABLE, _CONFIRMED),
    (Action.EDIT, _INFERRED): _Rule(_USER, _REVIEWABLE, _CONFIRMED),
    (Action.EDIT, _CONFIRMED): _Rule(_USER, _REVIEWABLE, _CONFIRMED),
    (Action.EDIT, _DEFINED): _Rule(_USER, _ONLY_RULES, _DEFINED),
    (Action.PROMOTE, _CONFIRMED): _Rule(_USER, _ONLY_RULES, _DEFINED),
    (Action.DEMOTE, _CONFIRMED): _Rule(_USER, _REVIEWABLE, _INFERRED),
    (Action.DEMOTE, _DEFINED): _Rule(_USER, _ONLY_RULES, _CONFIRMED),
    (Action.REJECT, _INFERRED): _Rule(_USER, _REVIEWABLE, _INFERRED),
    (Action.DEFER, _INFERRED): _Rule(_USER, _REVIEWABLE, _INFERRED),
    (Action.RETURN_TO_INBOX, _INFERRED): _Rule(_USER, _REVIEWABLE, _INFERRED),
    (Action.FLAG_RE_REVIEW, _INFERRED): _Rule(_SYSTEM, _REVIEWABLE, _INFERRED),
    (Action.FLAG_RE_REVIEW, _CONFIRMED): _Rule(_SYSTEM, _REVIEWABLE, _CONFIRMED),
    (Action.FLAG_RE_REVIEW, _DEFINED): _Rule(_SYSTEM, _ONLY_RULES, _DEFINED),
    (Action.KEEP_AS_IS, _CONFIRMED): _Rule(_USER, _REVIEWABLE, _CONFIRMED),
    (Action.KEEP_AS_IS, _DEFINED): _Rule(_USER, _ONLY_RULES, _DEFINED),
}


def next_state(
    actor: Actor, action: Action, kind: ItemKind, state: TrustState | None
) -> TrustState:
    """The state after this action, or `Rejected` if the rules don't allow it. None means new."""
    rule = _RULES.get((action, state))
    if rule is None or actor not in rule.actors or kind not in rule.kinds:
        raise Rejected(f"{actor} can't {action} a {kind} in state {state}")
    return rule.result
