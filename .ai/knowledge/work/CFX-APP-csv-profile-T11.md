---
id: CFX-APP-csv-profile-T11
title: "Earlier runs"
feature: CFX-APP-csv-profile
feature_revision: 4
stories: [US-07]
criteria: [AC-35, AC-36, AC-37, AC-38]
blocked_by: [CFX-APP-csv-profile-T08]
state: blocked
external_key: null
status: CONFIRMED
---

# Earlier runs

## Outcome
A user can list and open earlier runs, and can tell when results used earlier settings or may be out of date. Everything survives a restart.

## Acceptance checklist
- [ ] AC-35 (US-07): Given several runs of a source, then the user can list them with mode, status and time, open any completed one, and the newest completed run with the current settings is shown by default.
- [ ] AC-36 (US-07): Given a run made before a correction, then it is labelled "used earlier settings", and its findings no longer appear as current.
- [ ] AC-37 (US-07): Given a file whose size or modified time differs from the latest run's, then the Profile tab says the results may be out of date.
- [ ] AC-38 (US-07): Given a restart, then completed runs, their metrics and findings are as they were, and a run that was in progress shows as `interrupted`.

## Expected evidence
Tests naming each criterion, including a restart during a run.
- From T04: a source whose only run is `interrupted` gets an empty Profile tab, while the header says "Interrupted". AC-38 should say so in the tab.
