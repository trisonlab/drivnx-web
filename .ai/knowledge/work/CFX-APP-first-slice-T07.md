---
id: CFX-APP-first-slice-T07
title: "Trust-state core"
feature: CFX-APP-first-slice
feature_revision: 7
stories: [US-08]
criteria: [AC-35, AC-36, AC-37, AC-38, AC-40, AC-41, AC-48]
blocked_by: [CFX-APP-first-slice-T03]
state: ready
external_key: null
status: CONFIRMED
---

# Trust-state core

## Outcome
All M1 entities can be saved and read back, and every allowed trust-state transition records its ProvenanceEvent in the same transaction. AI and system actors can't promote anything, and a failure halfway leaves nothing behind.

## Acceptance checklist
- [ ] AC-35 (US-08): Given the M1 entities in Section 12, then each one can be created and read back from SQLite through the artifact service.
- [ ] AC-36 (US-08): Given each allowed transition in Section 12, as amended by ADR 0003, then it succeeds and records a ProvenanceEvent in the same transaction. This covers New → Observed, Inferred or Confirmed; Inferred → Confirmed; Confirmed → Defined; edits; and both demotions.
- [ ] AC-37 (US-08): Given an AI or system actor, when it tries Inferred → Confirmed or Defined, then the domain layer rejects it and nothing is written.
- [ ] AC-38 (US-08): Given a user rejects or defers an item, then it stays Inferred and leaves the review inbox, the outcome is in provenance, and a deferred item can return to the inbox.
- [ ] AC-40 (US-08): Given a failure halfway through a transition, then neither the state change nor its ProvenanceEvent is saved.
- [ ] AC-41 (US-08): Given two concurrent Accepts on one item, then one succeeds and the other is rejected by the transition check, and neither gets `SQLITE_BUSY`. Given a profile run writing observations, then a review action finishes within `busy_timeout`.
- [ ] AC-48 (US-08): Given any completed test run of this slice, then a scan of `workspace.db` finds no credential-shaped value.

## Expected evidence
Tests naming each criterion, including a forced mid-transition failure, two concurrent Accepts, and a review action during observation writes.

## From the T04 review
- `save_settings` in `packages/artifacts/settings.py` writes without `BEGIN IMMEDIATE`, which ADR 0002 point 3 requires. It couldn't be made to fail, because a single statement has no earlier read to conflict with. Move it onto the write helper this ticket builds.
