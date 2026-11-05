---
id: CFX-APP-csv-profile-T01
title: "Measure and record the rule"
feature: CFX-APP-csv-profile
feature_revision: 1
stories: [US-08]
criteria: [AC-42]
blocked_by: []
state: ready
external_key: null
status: CONFIRMED
---

# Measure and record the rule

## Outcome
The all-text read and the duplicate-row check are timed on a 1 GB CSV, so the targets are confirmed or a spec change is raised before any profiling is built. The R3 rule and the profile-caching decision are written down where the threat register and the direction doc expect them.

## Acceptance checklist
- [ ] AC-42 (US-08): Given the threat register and the direction doc, then R3 records the rule for profiling and what stays open, and an ADR records the rule and the profile-caching decision.

## Expected evidence
Measured figures in this ticket, on a real disk. The new ADR, the updated R3 row, and notes in Sections 5, 7 and 10 of the direction doc. AC-42 is checked by reading the documents.

## From planning
- If a timing misses a target, stop and raise a spec change before T04.
