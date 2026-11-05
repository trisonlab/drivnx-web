---
id: CFX-APP-csv-profile-T07
title: "Runs that can't go ahead"
feature: CFX-APP-csv-profile
feature_revision: 1
stories: [US-01, US-02]
criteria: [AC-06, AC-11, AC-12]
blocked_by: [CFX-APP-csv-profile-T04]
state: blocked
external_key: null
status: CONFIRMED
---

# Runs that can't go ahead

## Outcome
A file that's gone, unreadable or changed, or a correction attempted during a run, gives a clear message and never leaves misleading results.

## Acceptance checklist
- [ ] AC-06 (US-01): Given a profile run in progress, when the user tries to save a correction, then it is refused with a clear message.
- [ ] AC-11 (US-02): Given a file that has moved, was deleted or can't be read, when a run starts or fails partway, then the user gets a clear message, a run that started ends as `failed`, and its results don't appear as current.
- [ ] AC-12 (US-02): Given a file whose columns no longer match the saved ones, when a run starts, then it is refused with a message pointing to the source's settings, and saving those settings reads the columns again.

## Expected evidence
Tests naming each criterion, including a file removed during a run and a file that changes during a run.

## From planning
- DuckDB's error messages quote rows, so a failed run must not save or log them. The preview already avoids this.
