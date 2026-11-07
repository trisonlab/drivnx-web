---
id: CFX-APP-csv-profile-T06
title: "Full profile and cancel"
feature: CFX-APP-csv-profile
feature_revision: 2
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
- From T04: pressing Ctrl+C during a run exits cleanly and the next start marks it `interrupted`, but DuckDB's binding prints "leaked function" warnings, as the job's thread still holds a connection. No rows or names were in the output. Fast runs are short, and a full profile makes this much more likely, so stopping a job should also cover shutting down.
- From planning T07: DuckDB 2.0.0 silently drops every row after an unterminated quote that comes after the first 2,046 rows. T07 catches this in a fast run with a lax count of the same read when the run would claim the whole file. A full profile always claims the whole file, so it needs the same check across all of it, and its cost on a large file needs measuring.
