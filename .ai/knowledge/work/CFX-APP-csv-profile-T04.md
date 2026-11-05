---
id: CFX-APP-csv-profile-T04
title: "Fast profile, end to end"
feature: CFX-APP-csv-profile
feature_revision: 1
stories: [US-02, US-03]
criteria: [AC-07, AC-09, AC-13, AC-15, AC-16, AC-17]
blocked_by: [CFX-APP-csv-profile-T01, CFX-APP-csv-profile-T02]
state: blocked
external_key: null
status: CONFIRMED
---

# Fast profile, end to end

## Outcome
The thin path through every layer: a user presses Fast profile, a background run reads the first rows, and the Profile tab shows the first metrics with their labels while the header shows the run's status.

## Acceptance checklist
- [ ] AC-07 (US-02): Given a connected CSV and no run in progress, when the user chooses Fast profile, then a run reads at most the first 100,000 rows and completes with its results shown.
- [ ] AC-09 (US-02): Given a run in progress, then the header's profile status and the Profile tab show that it's running, and a second run is refused with a clear message.
- [ ] AC-13 (US-02): Given a profile job, then it creates nothing in the current directory, and any spill files are inside `<workspace>/duckdb-tmp/`.
- [ ] AC-15 (US-03): Given any metric, then the screen shows its scope (whole file or first rows), its precision (exact or approximate) and its run's time and ID, and results from different scopes are never mixed without a label.
- [ ] AC-16 (US-03): Given a fast profile of a file with more than 100,000 rows, then its metrics are labelled "first 100,000 rows, not random", and no total row count is claimed.
- [ ] AC-17 (US-03): Given a fast profile of a file with at most 100,000 rows, then its metrics are labelled as the whole file.

## Expected evidence
Tests naming each criterion, and a run on files above and below 100,000 rows.

## From planning
- `packages/profiling` has to be added to the wheel's package list in `pyproject.toml`.
- The first slice's AC-33 checks in `App.test.tsx` and `tests/e2e/test_first_launch.py` change for the status and the Profile tab. The other tabs stay marked as not available.
- T01 measured with DuckDB limited to 4 GB, and peak memory reached about 3 GB. DuckDB's default is 80% of the machine's memory, so profile jobs should set a limit rather than risk slowing the user's other programs.
