---
id: CFX-APP-first-slice-T08
title: "Profile runs and history rules"
feature: CFX-APP-first-slice
feature_revision: 8
stories: [US-08]
criteria: [AC-39, AC-42, AC-43]
blocked_by: [CFX-APP-first-slice-T07]
state: blocked
external_key: null
status: CONFIRMED
---

# Profile runs and history rules

## Outcome
Interrupted runs are marked at startup, history stays append-only, and a contradicting newer run flags an item "needs re-review" without changing its state. Settle how the flag is stored: the plan is to derive it from provenance events and the latest completed run.

## Acceptance checklist
- [ ] AC-39 (US-08): Given a newer completed profile run contradicts an item's evidence, then its state is unchanged and it is flagged "needs re-review" until a user reviews it again.
- [ ] AC-42 (US-08): Given the artifact service's full test suite, then it never updates or deletes observations, revisions or provenance rows, and a ProfileRun changes only its status and end time, once.
- [ ] AC-43 (US-08): Given a run left `running` when the runtime stopped, when the next runtime starts, then it is marked `interrupted`, and findings ignore it.

## Expected evidence
Tests naming each criterion, and the "needs re-review" storage decision recorded in the spec's open questions.

## From T07
- `start_profile_run`, `add_observations`, `read_profile_run` and `read_observations` are in `packages/artifacts/profiles.py`. Nothing ends a run yet, so its status stays `running`. Ending one and marking `interrupted` runs at startup are this ticket's.
- `ProvenanceEvent.action` already records every review outcome, so "needs re-review" can be derived alongside them. `review_inbox` in `packages/artifacts/review.py` is the pattern.
- Use `write_transaction` from `packages/artifacts/store.py` for every write. `WorkspaceBusy` isn't mapped to an API error yet, as no route calls these functions.
- Found reviewing T07, and left for this ticket as it builds the code that writes these:
  - `add_observations` refuses more than `MAX_OBSERVATIONS_PER_TRANSACTION` (1,000), so a profile run must split larger relations into batches.
  - Refuse adding observations to a run that has finished. Today nothing checks the run's status.
  - A Finding's links aren't checked: it can have no observations, or observations from another run. Require at least one, all from its own run.
- `add_finding` takes an actor, and `NewObservation` takes a `dataset_id` first, also for a dataset-level observation.
