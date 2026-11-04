---
id: CFX-APP-first-slice-T09
title: "Contract rules"
feature: CFX-APP-first-slice
feature_revision: 8
stories: [US-08]
criteria: [AC-45, AC-46, AC-47]
blocked_by: [CFX-APP-first-slice-T07]
state: blocked
external_key: null
status: CONFIRMED
---

# Contract rules

## Outcome
Rules can exist as candidates, and only Defined rules are part of the contract. Promotion, demotion, deletion, one rule per slot and contract versions follow ADR 0003. ODCS export stays in M7.

## Acceptance checklist
- [ ] AC-45 (US-08): Given a Defined rule, when it is demoted, then a Confirmed revision is written, the rule leaves the contract, a new contract version is created, and its history and provenance stay intact. Given a rule is deleted, then a tombstone revision is written and no rows are removed.
- [ ] AC-46 (US-08): Given a slot already held by a Defined rule (ADR 0003 point 4), when a second rule is promoted into it, then it is rejected and nothing is written. Overlapping candidates are allowed, and changing a candidate creates no contract version.
- [ ] AC-47 (US-08): Given a rule written directly, then it goes New → Confirmed → Defined in one transaction with both events recorded.

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
