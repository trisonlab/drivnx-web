---
id: CFX-APP-first-slice-T08
title: "Profile runs and history rules"
feature: CFX-APP-first-slice
feature_revision: 1
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
