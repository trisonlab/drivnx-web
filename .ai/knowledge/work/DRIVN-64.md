---
id: DRIVN-64
title: "Parquet: findings, values on request and earlier runs"
feature: DRIVN-60
feature_revision: 1
stories: [US-03]
criteria: [AC-14]
blocked_by: [DRIVN-63]
state: done
external_key: DRIVN-64
status: CONFIRMED
---

# Parquet: findings, values on request and earlier runs

Plane: [DRIVN-64](https://app.plane.so/drivnx/browse/DRIVN-64/)

## Outcome
A completed Parquet run shows the CSV profile's metrics with their scope and precision, its findings and sensitive-field rules, values on request computed now from the same seeded row groups and never saved, and its earlier runs, and says when the file has changed since it ran.

## Acceptance checklist
- [x] AC-14 (US-03, this ticket's part: a Parquet file; a sheet's is DRIVN-68's): Given a completed run of `Orders` or `events.parquet`, then each column shows the CSV profile's metrics with their scope and precision; the CSV profile's findings and sensitive-field rules apply; values on request are computed now from the run's own rows (the sheet's first rows, or the same seeded row groups) and never saved; and earlier runs are listed and opened as a CSV's. When the file's size or modified time has changed since the run, then the Profile tab says "This file has changed since this profile ran, so these results may be out of date."

## Expected evidence
Integration tests for each metric's scope and precision on a Parquet column, a finding and a sensitive field, values on request from the run's own row groups with nothing saved, earlier runs listed and opened, and the changed-file message after a size or modified-time change. Vitest and an e2e of a completed run.

## Notes
A Parquet column keeps its native type, so it never has values that don't fit (the spec's Assumptions).

## Blocked by
DRIVN-63.

## Verification (2026-11-27, on `feat/drivn-64-parquet-findings` from `b6efe4a`, uncommitted)
| Criterion | Evidence | Result |
|---|---|---|
| AC-14: each column shows the CSV profile's metrics with their scope and precision | `test_parquet_results.py::test_each_column_has_the_csv_profiles_metrics_for_its_type[fast, full]`: counts for every column, lengths and masked patterns for text only, never values that don't fit, values counted by hand. `test_every_metric_says_what_it_was_measured_on_and_is_exact` (3 cases): `sample` or `full`, the row count `metadata`, all `exact`. Both passed at once (DRIVN-63 built it); they fail when every column gets text metrics, or a Parquet metric isn't `exact` | PASS |
| AC-14: the CSV profile's findings and sensitive-field rules apply | `test_the_csv_profiles_findings_and_sensitive_field_rules_apply`: possibly sensitive (name and shape), duplicate rows, missing values, possible identifier, single value. Passed at once (the file run's path is shared); fails when Parquet runs don't save masked patterns. Vitest "a sampled run's findings say they're from its rows, sampled by row group" (failed first: "in the first 100,000 rows"), which the e2e found | PASS |
| AC-14: values on request are computed now from the same seeded row groups and never saved | `test_a_fast_runs_values_come_from_its_own_seeded_slices` (seed 42: the range runs from the first slice's first row to the last slice's last; failed first with the 409). `test_a_full_runs_values_come_from_every_row`, `test_a_small_files_values_are_of_every_row_as_its_type[fast, full]` (values counted by hand). `test_values_are_never_saved_nor_kept_by_the_browser_and_are_cut_at_1000_characters`: `no-store`, cut at 1,000, the run's observations unchanged (fails without the header; a save is refused by the store). Refusals as a CSV's: a column the run doesn't have (404; failed first with a crash), possibly sensitive without the confirmation (failed first: shown), a file gone (failed first: an uncaught error), columns renamed or retyped (failed first: values from the wrong column), a value DuckDB can't read ("DrivnX couldn't read this file."; without it DuckDB's text quotes the value), a profile running (failed first: an uncaught error). Vitest "a sampled run's values say they're from its rows, sampled by row group" (failed first: "not random"), and "a full run's values say they're from every row, as its columns do" (failed first: "from the whole file"; from the review). A wide decimal's or a nanosecond timestamp's range rounds: a known limit, ticketed as DRIVN-73 | PASS |
| AC-14: earlier runs are listed and opened as a CSV's | `test_every_run_of_a_parquet_file_is_listed_and_any_completed_one_opens`; passed at once (shared). The e2e opens the earlier fast run from "Runs of this file" | PASS |
| AC-14: "This file has changed since this profile ran…" when its size or modified time changed | `test_a_run_on_an_unchanged_parquet_file_is_not_out_of_date`, `test_a_parquet_file_changed_since_its_run_is_said_to_have_changed[size, modified time]`; passed at once (shared), and the second fails when only the size is compared. Vitest "a run of a file that has changed since says its results may be out of date"; fails when the note is hidden for a Parquet run | PASS |
| Expected evidence: Vitest and an e2e of a completed run | `tests/e2e/test_parquet_results.py::test_a_completed_runs_findings_values_and_earlier_runs`: a fast profile of 200,000 rows, its 3 findings, the values of `id` with their histogram, a full profile, and the earlier run opened, with an automated accessibility check in each state. It checks the `id` row itself (a number has no patterns, whose label names the sample on any run), so it fails when the earlier run isn't opened (from the review) | PASS |

Decided in building it (2026-11-27): a Parquet file whose columns changed since its run refuses values with the run's
own "The file's columns no longer match the saved ones.", as a Parquet file has no settings to save.

Review (2026-11-27, the independent reviewer): no blockers. Fixed: a full run's values said "the whole file" where its
columns say "every row, N rows", and the e2e's check that the earlier run opened couldn't fail. Kept: running out of
memory working out a Parquet column's values says "DrivnX couldn't read this file.", as a CSV's does. Recorded and
ticketed, as the user chose: a decimal wider than 15 significant digits, or a nanosecond timestamp, shows a rounded
minimum, maximum, median and histogram (`issues/values-round-wide-numbers.md`, DRIVN-73).

Checks run, on the tree above (product code unchanged since the full run started at 01:16:23; one test added after):
- `uv run --frozen pytest tests/integration tests/golden`: 2712 passed, 8 skipped (as many skips as DRIVN-63's run),
  exit 0, in 8 min 53 s, with the PostgreSQL fixture from the local PostgreSQL 18.6 (`PGBIN`), as Docker here has no
  Compose; CI pins 18.7.
- `uv run --frozen pytest tests/e2e`: 100 passed, exit 0, in 3 min 54 s.
- After the last test: the Parquet, values, findings and earlier-runs integration files and the two values e2e files,
  76 passed, exit 0. `pnpm --dir apps/web exec vitest run`: 272 passed. `pnpm --dir apps/web exec tsc --noEmit`: exit 0.

Not verified:
- `Orders` (a sheet) is DRIVN-68's part of AC-14.
- Known limit: values of a decimal wider than 15 significant digits, or a nanosecond timestamp, are rounded in their
  minimum, maximum, median and histogram (DRIVN-73). Most likely the same for a CSV's and a PostgreSQL column's
  (INFERRED).
- Values of a full run on a 1 GB Parquet file: they load every row as a run does, under the same memory limit and
  spill folder, but their time and memory weren't measured.
- After the file changes with its columns the same, values come from the same row positions, which are no longer the
  run's rows. The Profile tab then says the results may be out of date, as for a CSV.
