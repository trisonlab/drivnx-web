---
id: CFX-APP-first-slice-T07
title: "Trust-state core"
feature: CFX-APP-first-slice
feature_revision: 8
stories: [US-08]
criteria: [AC-35, AC-36, AC-37, AC-38, AC-40, AC-41, AC-48, AC-55]
blocked_by: [CFX-APP-first-slice-T03]
state: done
external_key: null
status: CONFIRMED
---

# Trust-state core

## Outcome
All M1 entities can be saved and read back, and every allowed trust-state transition records its ProvenanceEvent in the same transaction. AI and system actors can't promote anything, and a failure halfway leaves nothing behind.

## Acceptance checklist
- [x] AC-35 (US-08): Given the M1 entities in Section 12, then each one can be created and read back from SQLite through the artifact service, with everything it was saved with. A DictionaryEntry also holds its assumptions (Section 3).
- [x] AC-36 (US-08): Given each allowed transition in Section 12, as amended by ADR 0003, then it succeeds and records a ProvenanceEvent in the same transaction. This covers New → Observed, Inferred or Confirmed; Inferred → Confirmed; Confirmed → Defined; edits; and both demotions. A Finding is New → Inferred, written by a heuristic or AI (Section 5), and records its event the same way.
- [x] AC-37 (US-08): Given an AI or system actor, when it tries Inferred → Confirmed or Defined, then the domain layer rejects it and nothing is written.
- [x] AC-38 (US-08): Given a user rejects or defers an item, then it stays Inferred and leaves the review inbox, the outcome is in provenance, and a deferred item can return to the inbox.
- [x] AC-40 (US-08): Given a failure halfway through a transition, then neither the state change nor its ProvenanceEvent is saved.
- [x] AC-41 (US-08): Given two concurrent Accepts on one item, then one succeeds and the other is rejected by the transition check, and neither gets `SQLITE_BUSY`. Given a profile run writing observations, then a review action finishes within `busy_timeout`.
- [x] AC-48 (US-08): Given any completed test run of this slice, then a scan of `workspace.db` finds no credential-shaped value.
- [x] AC-55 (US-08): Given a field with a Confirmed dictionary entry, when a second entry for that field would become Confirmed, then it is rejected and nothing is written. Inferred candidates for the field are allowed, and demoting the Confirmed entry frees the field.

## Expected evidence
Tests naming each criterion, including a forced mid-transition failure, two concurrent Accepts, and a review action during observation writes.

## From the T04 review
- `save_settings` in `packages/artifacts/settings.py` writes without `BEGIN IMMEDIATE`, which ADR 0002 point 3 requires. It couldn't be made to fail, because a single statement has no earlier read to conflict with. Move it onto the write helper this ticket builds.

## Decisions in building this
- **Only a ContractRule can be Defined.** Section 3 says Defined comes "via the contract", and ADR 0003 says a confirmed fact is promoted into a rule. A DictionaryEntry stops at Confirmed.
- **Review inbox:** it's worked out from history, because history is append-only: Inferred items whose latest event isn't a reject or defer. A demoted item is back in it, because nothing has been decided about it since.
- **Reject has no way back to the inbox.** Section 12 only says Deferred items return. A rejected or deferred item can still be accepted, because it stays Inferred and Inferred -> Confirmed is always a user's call.
- **A Finding records its author and event as Inferred (spec revision 8).** Section 5 labels its interpretation as Inferred, and Section 12's table didn't list it. Nothing reviews a Finding further, so it never leaves Inferred.
- **A field has at most one Confirmed entry (AC-55).** This applies to entries for a field only. Dataset-level entries, with no field, aren't limited. The service checks it under the write lock, because "latest revision is Confirmed" can't be written as a database constraint.
- **An Accept leaves `reviewer` empty.** The author is already recorded, and M1 has one local user.
- **One transaction holds at most 1,000 observations (ADR 0002 point 3).** `add_observations` gathers them before taking the write lock.
- **Writers take turns in the process (ADR 0002 point 5).** `busy_timeout` alone let a review fail behind a busy profile run, in 4 of 12 runs when last measured, and the rate varies. A write that runs out of time waiting for its turn, a connection or SQLite's write lock gets `WorkspaceBusy`.
- **Credential scan (AC-48):** it runs at the end of every pytest session, over every `workspace.db*` the run created, including the browser tests'.
