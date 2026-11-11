---
id: DRIVN-31
legacy_id: CFX-APP-csv-profile-T11
title: "Earlier runs"
feature: CFX-APP-csv-profile
feature_revision: 4
stories: [US-07]
criteria: [AC-35, AC-36, AC-37, AC-38]
blocked_by: [DRIVN-28]
state: done
external_key: DRIVN-31
status: CONFIRMED
---

# Earlier runs

Plane: [DRIVN-31](https://app.plane.so/drivnx/browse/DRIVN-31/) · was `CFX-APP-csv-profile-T11`

## Outcome
A user can list and open earlier runs, and can tell when results used earlier settings or may be out of date. Everything survives a restart.

## Acceptance checklist
- [x] AC-35 (US-07): Given several runs of a source, then the user can list them with mode, status and time, open any completed one, and the newest completed run with the current settings is shown by default.
- [x] AC-36 (US-07): Given a run made before a correction, then it is labelled "used earlier settings", and its findings no longer appear as current.
- [x] AC-37 (US-07): Given a file whose size or modified time differs from the latest run's, then the Profile tab says the results may be out of date.
- [x] AC-38 (US-07): Given a restart, then completed runs, their metrics and findings are as they were, and a run that was in progress shows as `interrupted`.

## Expected evidence
Tests naming each criterion, including a restart during a run.
- From T04: a source whose only run is `interrupted` gets an empty Profile tab, while the header says "Interrupted". AC-38 should say so in the tab.

## Decisions in building this
- **Most of the backend was already there.** The run list (newest first, with mode, status and times), `current_settings`, and marking a run `interrupted` at startup came with DRIVN-18 and DRIVN-24. The tests for AC-35, AC-36 and AC-38 in `tests/integration/test_earlier_runs.py` passed without new code; they now name the criteria. AC-38's test restarts the real `drivnx` during a full run and compares the completed run's detail and findings before and after.
- **"Out of date" is worked out when a run's detail is read (AC-37).** `GET /api/profile-runs/{id}` has `file_changed`: whether the file's size or modified time now differs from those saved with the run. It's `null` when the file can't be looked at, as the source already says it's missing. It compares against the run being shown, so an opened earlier run says so too (INFERRED reading of "the latest run's").
- **The runs list is a list, not a table,** named "Runs of this file", newest first, under the results. A completed run that isn't shown has an Open button; the shown one says "Shown". Runs from before a correction say "Used earlier settings" (AC-36), and only appear when opened.
- **The findings panel follows the opened run.** Otherwise a finding's evidence link would focus a metric of a different run. The opened run is held in `Shell`, and goes back to the newest when another source is chosen or a profile is started. A run on earlier settings says its findings aren't current, in both panels.
- **An interrupted newest run says so in the tab (AC-38, from T04):** "This profile was interrupted when DrivnX stopped." As for a failed or cancelled run, the results below are from the run before, and say so only when they're the default ones.
