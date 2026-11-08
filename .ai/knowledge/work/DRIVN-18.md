---
id: DRIVN-18
legacy_id: CFX-APP-first-slice-T08
title: "Profile runs and history rules"
feature: CFX-APP-first-slice
feature_revision: 9
stories: [US-08]
criteria: [AC-39, AC-42, AC-43]
blocked_by: [DRIVN-17]
state: done
external_key: DRIVN-18
status: CONFIRMED
---

# Profile runs and history rules

Plane: [DRIVN-18](https://app.plane.so/drivnx/browse/DRIVN-18/) · was `CFX-APP-first-slice-T08`

## Outcome
Interrupted runs are marked at startup, history stays append-only, and a contradicting newer run flags an item "needs re-review" without changing its state. The flag is stored as a ProvenanceEvent (spec revision 9).

## Acceptance checklist
- [x] AC-39 (US-08): Given a newer completed profile run contradicts an item's evidence, then its state is unchanged and it is flagged "needs re-review" until a user reviews it again.
- [x] AC-42 (US-08): Given the artifact service's full test suite, then it never updates or deletes observations, revisions or provenance rows, and a ProfileRun changes only its status and end time, once.
- [x] AC-43 (US-08): Given a run left `running` when the runtime stopped, when the next runtime starts, then it is marked `interrupted`, and findings ignore it.

## Expected evidence
Tests naming each criterion, and the "needs re-review" storage decision recorded in AC-39.

## From T07
- `start_profile_run`, `add_observations`, `read_profile_run` and `read_observations` are in `packages/artifacts/profiles.py`. Nothing ends a run yet, so its status stays `running`. Ending one and marking `interrupted` runs at startup are this ticket's.
- `ProvenanceEvent.action` already records every review outcome, so "needs re-review" can be derived alongside them. `review_inbox` in `packages/artifacts/review.py` is the pattern.
- Use `write_transaction` from `packages/artifacts/store.py` for every write. `WorkspaceBusy` isn't mapped to an API error yet, as no route calls these functions.
- Found reviewing T07, and left for this ticket as it builds the code that writes these:
  - `add_observations` refuses more than `MAX_OBSERVATIONS_PER_TRANSACTION` (1,000), so a profile run must split larger relations into batches. Not done here: this ticket builds nothing that writes a run, so it falls to the profiling engine in M3.
  - Refuse adding observations to a run that has finished. Today nothing checks the run's status.
  - A Finding's links aren't checked: it can have no observations, or observations from another run. Require at least one, all from its own run.
- `add_finding` takes an actor, and `NewObservation` takes a `dataset_id` first, also for a dataset-level observation.

## Decisions in building this
- **"Needs re-review" is a ProvenanceEvent (spec revision 9).** An item is flagged when its latest event, leaving out Defer and Return to inbox, is a flag. So nothing is updated. Nothing raises the flag in this slice: what counts as a contradiction comes with the profiling work in M3.
- **Keep as is (spec revision 9):** only a user, only on a flagged Confirmed or Defined item, and it changes nothing else. Accept, Edit, Reject, Promote and Demote also clear the flag. Defer and Return to inbox don't, because neither reviews anything (the user's decision reviewing this ticket).
- **A newer run** means a completed run that ended after the item's latest event, so a review that came after a run isn't flagged by it. This relies on the system clock, and a run ending at the same moment as the latest event isn't newer. Any later event closes the door on an earlier run, so M3 has to write a run's flags as it finishes (in the spec's open questions).
- **A run that didn't complete takes no findings (the user's decision reviewing this ticket).** `read_findings` would never return one. A completed run still does, as an AI finding can come after the run it reads.
- **A flag isn't a decision.** It doesn't bring a deferred item back to the inbox, and a deferred item can still return. A rejected item can't be flagged, because only Accept or Edit could then clear it.
- **Marking interrupted runs is in the CLI, not `open_workspace`.** `open_workspace` also runs with other migration sets, and knows nothing about the domain tables. ADR 0001's startup sequence puts it right after opening.
- **An interrupted run's end time is when startup marked it,** because the real moment is unknown.
- **`read_findings` returns only completed runs' findings.** `read_finding` still reads any of them, because partial runs stay visible in history (ADR 0002 point 3).
- **The AC-42 watch** runs over every statement in the test process, like the AC-48 scan, and fails the run at the end. It uses SQLite's own authorizer, because a pattern over the SQL's text missed quoted and qualified table names, `UPDATE OR ABORT` and `WITH` (found verifying this ticket). The authorizer can't see what `REPLACE` overwrites, so that part reads the text. It doesn't see a `REPLACE` in a trigger or through the raw driver connection, `ALTER TABLE`, or a separate `sqlite3.connect` such as a separately started `drivnx`, but no route writes these tables yet. It also covers `contract_rule` and `dictionary_entry`, which ADR 0002 point 4 and `Item.subject` say never change. ADR 0002 point 4 leaves this to tests and not database triggers.
