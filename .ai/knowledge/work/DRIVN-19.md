---
id: DRIVN-19
legacy_id: CFX-APP-first-slice-T09
title: "Contract rules"
feature: CFX-APP-first-slice
feature_revision: 9
stories: [US-08]
criteria: [AC-45, AC-46, AC-47]
blocked_by: [DRIVN-17]
state: done
external_key: DRIVN-19
status: CONFIRMED
---

# Contract rules

Plane: [DRIVN-19](https://app.plane.so/drivnx/browse/DRIVN-19/) · was `CFX-APP-first-slice-T09`

## Outcome
Rules can exist as candidates, and only Defined rules are part of the contract. Promotion, demotion, deletion, one rule per slot and contract versions follow ADR 0003. ODCS export stays in M7.

## Acceptance checklist
- [x] AC-45 (US-08): Given a Defined rule, when it is demoted, then a Confirmed revision is written, the rule leaves the contract, a new contract version is created, and its history and provenance stay intact. Given a rule is deleted, then a tombstone revision is written and no rows are removed.
- [x] AC-46 (US-08): Given a slot already held by a Defined rule (ADR 0003 point 4), when a second rule is promoted into it, then it is rejected and nothing is written. Overlapping candidates are allowed, and changing a candidate creates no contract version.
- [x] AC-47 (US-08): Given a rule written directly, then it goes New → Confirmed → Defined in one transaction with both events recorded.

## Expected evidence
Tests naming each criterion, including a demoted rule leaving the contract and a second rule rejected from an occupied slot.

## From T07
- `create_rule`, `promote`, `demote` and `edit` are in `packages/artifacts/review.py`, and the tables match ADR 0003 point 2. They handle trust state and provenance only.
- Still this ticket's: contract versions (`contract_rule_revision.contract_version` stays empty and `current_version` stays 1), one rule per slot, tombstones (`is_tombstone` is never set), the direct New -> Confirmed -> Defined write, and checking `params` against the Pydantic union on `rule_type`, which is stored unchecked for now.
- Found reviewing T07, and left for this ticket as it decides what a valid rule is:
  - Check a rule's fields against its type. A `required` rule with no fields reached Defined.
  - Refuse rule fields from a dataset other than the contract's. Today any field is accepted.
  - List a contract's rules. Nothing does yet, and a rule can only be read by its id.
- `read_item` gives a rule's contract, type and fields in `Item.subject`. It replaced `read_rule_fields`.

## Decisions in building this
- **`define_rule` is the direct write (AC-47).** `create_rule` still refuses Defined, so nothing reaches it by accident. It writes revisions 1 (Confirmed) and 2 (Defined) with both events, and only revision 2 has a contract version.
- **Every contract version gets a `contract_revision` row,** copying purpose and limitations, because `read_contract` reads the row at the contract's current version. Editing purpose or limitations isn't built: no criterion asks for it, and the contract editor is M7.
- **`rules_in_contract` takes a version,** because that query is how ADR 0003 point 3 defines the contract, and the only way to prove `contract_version` is right. `list_rules` gives every rule that isn't deleted, candidates included.
- **Deleting (`delete_rule`, `Action.DELETE`):** only a user, on a rule in any state. The tombstone keeps the state it had, and `from_state` and `to_state` are the same. A Defined rule's deletion creates a contract version. Afterwards `read_item` and every action say `ItemNotFound`, while `history` still reads. Section 12 lists no delete as it isn't a transition, and AC-45 and ADR 0002 point 4 ask for it.
- **Slots (ADR 0003 point 4):** refused with `Rejected`, inside the write lock, on `promote` and `define_rule`. An edit keeps its slot, as a rule's fields never change. A relationship's slot is its set of subject and target pairs, so the same pairs in another order compete, and the same fields paired differently don't.
- **A range and its field's type rule must agree only among Defined rules,** as candidates can overlap. The check runs on becoming Defined and on editing a Defined rule, from either side.
- **Params and fields refuse with `ValueError`,** and a refusal names no values, as accepted values and range ends can come from source data. `params` is stored as given. The type rule's `logical_type` is ODCS v3.2.0's list, read from its JSON Schema: string, date, timestamp, time, number, integer, object, array, boolean, map and vector. A range's ends must be readable as its type: ISO 8601 text for dates and times, with T, t or a space before a timestamp's time, and a minimum can't be above a maximum. A lone surrogate is refused as an accepted value, as a response could never send it back and a rule's history can't be cleaned up.
- **`rules_in_contract` refuses a version the contract never had, and a contract that doesn't exist,** both with `ValueError`, so an exporter can't write a document for a version that never was.
- **Accepted values:** 1 and 1.0 are one number, while true and 1 are different values, as JSON keeps them apart.
- **`packages/domain/rules.py`** holds what a valid rule is and which rules compete, with no database. ADR 0003 says the domain rule types belong in `packages/domain`. Pydantic is now a direct dependency, as this imports it.

## Left as they are, from the review
Asked and decided at review:
- An edit that changes nothing still creates a contract version when the rule is Defined, as ADR 0003 point 3 lists "a Defined rule being edited".
- `edit(ref, None)` creates a version and keeps the content. The type hint excludes `None`, and nothing calls it that way.
- A relationship from a field to itself can reach Defined. ADR 0003 doesn't address it, so wait for M7's export to show whether it matters.

For the ticket that adds the contract routes:
- `add_contract` with an unknown dataset and `read_contract` with an unknown id raise raw database errors, from before this ticket.
- `list_rules` gives an empty list for a contract that doesn't exist, while `rules_in_contract` refuses it.
