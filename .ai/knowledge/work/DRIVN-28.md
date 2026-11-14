---
id: DRIVN-28
legacy_id: CFX-APP-csv-profile-T08
title: "Findings with evidence"
feature: CFX-APP-csv-profile
feature_revision: 5
stories: [US-04]
criteria: [AC-21, AC-22, AC-23, AC-24, AC-25, AC-26]
blocked_by: [DRIVN-26]
state: done
external_key: DRIVN-28
status: CONFIRMED
---

# Findings with evidence

Plane: [DRIVN-28](https://app.plane.so/drivnx/browse/DRIVN-28/) · was `CFX-APP-csv-profile-T08`

## Outcome
Completed runs produce rule-based findings in the right panel, each showing what was observed and inferred, why it matters, what to do, and a link to its evidence.

## Acceptance checklist
- [x] AC-21 (US-04): Given a run that meets a finding's condition, then a finding of that kind appears for each column or dataset concerned: possible identifier, significant missing values, single-value column, values that don't fit the type, or possibly sensitive field. A run that doesn't meet a condition creates no finding of that kind.
- [x] AC-22 (US-04): Given a full profile of a file with fully duplicated rows, then a duplicate-rows finding appears with the count. Given a fast profile, then no duplicate-rows finding appears, and the screen says the check needs a full profile.
- [x] AC-23 (US-04): Given a finding, then it shows what was observed, what's inferred, why it matters, the recommended action, and its Inferred state, and its evidence link opens the cited metric with its run.
- [x] AC-24 (US-04): Given a finding from a fast profile that read only the first rows, then its text says "in the first 100,000 rows".
- [x] AC-25 (US-04): Given a saved finding, then its saved text holds no column name and no value from the file, and it cites at least one observation.
- [x] AC-26 (US-04): Given a run that was cancelled, failed or interrupted, then it creates no findings and earlier findings stay as they were.

## Expected evidence
Tests naming each criterion, and a check that saved finding text holds no column name or file value.

## From planning
- AC-21 is only partly delivered here: possible identifier, significant missing values, single-value column and values that don't fit the type. T09 finishes it.
- The `finding` table has no field for a finding's kind, so this adds one.
- The first slice's AC-33 checks change for the right panel's findings section.

## What planning measured
- **Counting duplicate rows is cheap enough.** On a 1.1 GB, 5-column file of 26,000,000 rows on a RAM disk, with DuckDB limited to 4 GB, copying every row took 9.27 s and `count(*)` minus the count of distinct rows took 2.07 s. Every row was distinct, the costliest case. The process peaked at 3.81 GB, within the limit and above ADR 0004's 3.06 GB.
- **What exists:** `add_finding` saves a finding's interpretation, why it matters, recommended action and cited observations. It refuses a run that didn't complete, and accepts one that's running or completed. Nothing outside tests calls it. Readers show findings of completed runs only. There's no field for a finding's kind, no findings route, and the right panel only says "Not available yet".

## Plan agreed with the user
Decided by the user, 2026-11-07, taking every recommendation:
- **Findings are saved in the transaction that marks the run `completed`,** so a cancelled, failed or interrupted run never has findings, and a completed one never has half of them (AC-26). Rejected: saving them before the run ends, which a later cancel would leave behind, hidden.
- **The rules live in `packages/profiling`,** as a pure function over the run's saved observations, so a finding's evidence is exactly what was saved. The direction doc gives the profiling engine "finding candidates".
- **Thresholds:** possible identifier, at least 99% of non-missing values distinct and at least two non-missing values (spec revision 4, from reviewing T08; it was one). Significant missing values, at least 20% missing (spec). Single-value column, one distinct value and at least 2 non-missing values, so a one-row file isn't both an identifier and single-value. Values that don't fit the type, at least one; text never has any. Duplicate rows, full profile only, counting rows that repeat an earlier row (3 identical rows count 2).
- **A migration adds `finding.kind`,** nullable and checked against the six kinds, T09's included. SQLite only adds a required column with a default, and a default kind would be untrue. No workspace has findings yet, and the code requires a kind.
- **Saved text is fixed per kind,** with no column name, value or number (AC-25). The observed line is built on screen from the cited observation.
- **Findings show in the right panel's Findings section for the run the Profile tab shows,** the newest completed run on current settings, in the fixed order by kind and then the column's position, each with an Inferred badge. Its evidence link moves to the cited column's row on the Profile tab and focuses the metric's cell, with the run's ID in the table's caption. The panel's other sections stay marked, and the first slice's AC-33 tests change for Findings only.
- **`GET /api/findings?profile_run_id=…`** returns each finding's kind, saved text, state and cited observations, each with its metric, value, scope and current column name.
- **Spec revision 3:** AC-24 applies "in the first 100,000 rows" only to a fast profile that read only the first rows, matching AC-17's labels.

Build order:
0. Migration `0006` adds `finding.kind`, and `NewFinding` and `Finding` carry it.
1. `findings_for(observations)` in `packages/profiling/findings.py`, tested at each threshold's edge.
2. A full profile saves a `duplicate_rows` observation, and a fast one doesn't.
3. `finish_profile_run(…, findings=…)` saves findings with `completed`. Tested with a file meeting every kind, cancelled, failed and interrupted runs, and a raw scan of saved findings for distinctive names and values.
4. `GET /api/findings`.
5. The Findings section, the full-profile note on duplicates, the evidence link, and the AC-33 changes, with Vitest and Playwright tests.
6. The full suites, a full profile with duplicates on 1 GB through the API, and a real-browser probe.

Out of scope: possibly-sensitive findings and their note (T09), findings stopping being current after a correction (AC-36, T10), prioritisation (M5), and an Evidence section in the panel.

## What building it showed
- **The rules read only saved observations,** in `packages/profiling/findings.py`, and need no file. Counts decide the thresholds, not the rounded percentages, so 99 of 100 distinct is an identifier and 98 isn't. A table with no rows, or a column with no values, gets no finding, as there's nothing to divide by.
- **A full profile counts duplicate rows over every column it read,** extra columns from a wider row included, as rows minus distinct rows. The count is saved as `duplicate_rows` and shown on the Profile tab, as a finding's evidence must be there to link to.
- **Findings were looked for in a run that saved nothing.** A cancel before any observation was saved left the rules without `rows_read`, and a `KeyError` escaped the job's thread, while the run still ended `cancelled`. A cancelled run now skips the rules, and the test checks no error escapes.
- **The guard that keeps findings from a cancelled run** only matters when a cancel lands after the rules ran. A test cancels at that moment, and handing the findings to any ending makes the run never end, as the store refuses findings on anything but `completed`.
- **The screen builds what was observed from the cited observations,** with "in the first 100,000 rows" when they come from the first rows only. A finding's evidence link focuses the cited cell on the Profile tab, or the duplicate count's line.
- **The GET routes' check covers `/api/findings`,** which needs its run's ID, so reading it changes no workspace data (AC-34).
- **Measured on the 1.1 GB file through the API,** on a RAM disk, three times: a full profile, with the duplicate count and findings, took 17.96 to 18.68 s for 26,000,000 rows, about 2.5 s more than T06's. The target is 60 s. That file meets no finding's condition, so none were saved.
- **A real browser on the installed wheel** shows the five findings in order, each with its observed line and Inferred badge, and the evidence link focuses the cited cell. The single-value finding's reason repeated what was observed, so it now says why it matters.

## From reviewing T08
- **A possible identifier needs two non-missing values** (spec revision 4). With one, a column of a single value among blanks was called one, as one value is always 100% distinct. A one-row file now gets no finding.
- **The run's ending held the write lock long on wide files.** Findings were saved one at a time, with a query and an insert per cited observation and an event each, so on a RAM disk 1,500 columns (4,501 findings) took 1.25 s, 5,000 columns 4.43 s, and at 7,000 a waiting review gave up after its 5 s. They're now saved in a few statements, with one query to check the cited observations belong to their run: 0.16 s, 0.60 s and 1.01 s, and a waiting review got through each time. ADR 0002 records it. `record_event` now goes through a bulk `record_events`, so events have one code path.
- **"Profile this file to see what it finds." showed too often:** while runs were loading, when they couldn't be read, and with no file connected. It now shows once runs are known and a file is chosen, and runs that can't be read give the findings' error line.
- **Store tests now hold `finish_profile_run`'s two guards:** findings only with `completed`, and only the run's own. The first test written for the second cited an observation of the run being ended, so a different check caught it; it now cites the other run's own observation, which only this guard stops.

## Verification
Verified 2026-11-07 on the uncommitted branch `feat/cfx-app-csv-profile-t08-findings-with-evidence`, after the review's fixes, against spec revision 4.

| Criterion | Evidence | Result |
|---|---|---|
| AC-21 | The rule tests for each kind at its threshold (identifier 99% and 98% and two values, one value among blanks no identifier, missing 20% and 19%, single-value with 3 rows and 1 row, a value that doesn't fit, a column with no values, a table with no rows, the fixed order), `test_a_completed_run_saves_a_finding_for_each_column_or_file_concerned`, the Vitest tests for each kind's observed line and the empty states, and the Playwright test `test_a_full_profiles_findings_show_with_their_evidence`, all passing. Possibly sensitive findings are T09's, as the ticket says. | PASS (for this ticket's kinds) |
| AC-22 | `test_a_full_profile_counts_rows_that_copy_another` (3 copies counted on a full profile, none on a fast one), the rule tests for duplicates, the Vitest tests for the count's line and the note "Duplicate rows are checked only by a full profile." after a fast profile, and the Playwright test, all passing. | PASS |
| AC-23 | `test_a_runs_findings_say_what_was_observed_inferred_and_why_with_their_evidence`, `test_only_that_runs_findings_are_given_and_only_once_it_has_completed`, the Vitest tests for what each finding shows, its Inferred badge, its evidence link to the cited cell or line, and the error states, and the Playwright test, all passing. A real browser's evidence link focused `note`'s Missing cell with the run's ID in the caption. | PASS |
| AC-24 | The Vitest test "a finding from the first rows only says so" (revision 3), and the exact observed lines of whole-file findings, which don't say it, all passing. | PASS |
| AC-25 | `test_saved_findings_hold_no_column_name_or_value_and_cite_evidence` (planted names and values absent from every saved finding, each citing at least one observation; seen failing with a column name added), passing. | PASS |
| AC-26 | `test_a_run_that_doesnt_complete_saves_no_findings_and_earlier_ones_stay` (failed and cancelled), `test_a_cancel_after_findings_are_worked_out_still_saves_none`, `test_findings_are_saved_only_with_a_completed_ending`, `test_a_run_ends_only_with_its_own_findings`, the shutdown test (an interrupted run has none), and the Vitest test that an earlier run's findings stay after a cancel, all passing. | PASS |

Checks run: `uv run --frozen pytest tests --ignore=tests/e2e` (2095 passed, 6 skipped: files that can't be connected, by design), `uv run --frozen pytest tests/e2e` (63 passed), `pnpm --dir apps/web test` (137 passed), `pnpm --dir apps/web build` (type-check and build, exit 0), `uvx ruff check packages apps tests testdata` (all passed), `git diff --check` (clean), `ai-skills knowledge check` (consistent). The suites started after the last code change. A run's ending with 21,001 findings from 7,000 columns held the write lock for 1.01 s on a RAM disk, and a waiting review got through.

Not verified: CI, as there's no remote yet. The 1.1 GB time (17.96 to 18.68 s) and the real-browser probe are from before the review's fixes, which changed how findings are written, not what; the Playwright test of findings ran after them. Times are from a RAM disk; T12 measures the real product on a disk.
