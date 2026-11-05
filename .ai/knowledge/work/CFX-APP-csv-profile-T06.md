---
id: CFX-APP-csv-profile-T06
title: "Full profile and cancel"
feature: CFX-APP-csv-profile
feature_revision: 1
stories: [US-02, US-03]
criteria: [AC-08, AC-10, AC-18]
blocked_by: [CFX-APP-csv-profile-T05]
state: blocked
external_key: null
status: CONFIRMED
---

# Full profile and cancel

## Outcome
A user can run a whole-file profile with exact results and a row count, and can cancel any run in progress.

## Acceptance checklist
- [ ] AC-08 (US-02): Given a connected CSV, when the user chooses Full profile, then a run reads every row and completes with exact results and the file's row count.
- [ ] AC-10 (US-02): Given a run in progress, when the user cancels it, then it stops within the target under Non-functional, its status is `cancelled`, and no results or findings from it appear as current.
- [ ] AC-18 (US-03): Given a full profile, then its masked patterns are labelled as coming from the first 100,000 rows, and its other metrics as the whole file.

## Expected evidence
Tests naming each criterion, and the cancel time measured on a large file.

## From planning
- Test a cancel arriving just as a run finishes. A run's status can only change once.
