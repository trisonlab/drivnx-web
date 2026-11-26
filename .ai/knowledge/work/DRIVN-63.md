---
id: DRIVN-63
title: "Parquet: fast and full profile, and cancel"
feature: DRIVN-60
feature_revision: 1
stories: [US-03]
criteria: [AC-12, AC-13, AC-30, AC-31]
blocked_by: [DRIVN-62]
state: done
external_key: DRIVN-63
status: CONFIRMED
---

# Parquet: fast and full profile, and cancel

Plane: [DRIVN-63](https://app.plane.so/drivnx/browse/DRIVN-63/)

## Outcome
A user profiles a Parquet file. A fast profile reads about 100,000 rows from row groups chosen with the run's seed, so the same seed reads the same rows, reads every row of a small file, and samples within a file's one huge row group. The row count comes from the metadata. A full profile reads every row, a cancel stops a run within 5 s, and a file that moved or can't be read fails the run with its message.

## Acceptance checklist
- [x] AC-12 (US-03): Given `events.parquet` and a run with the seed 42, when the user starts a fast profile, then it reads about 100,000 rows from row groups chosen with that seed, labelled "about 100,000 rows, sampled by row group", and the row count "40,000,000 rows" is labelled "from the file's metadata". A second fast run with the seed 42 reads the same rows. A full profile reads all 40,000,000.
- [x] AC-13 (US-03, this ticket's part: a Parquet file; a sheet's is DRIVN-68's): Given a full profile of a 1,000,000-row sheet or a 1 GB Parquet file, when the user cancels it 3 s after it starts, then it stops within 5 s, its status is `cancelled`, and none of its results or findings appear as current.
- [x] AC-30 (US-03): Given a Parquet file of 60,000 rows, when the user starts a fast profile, then it reads every row, labelled "every row, 60,000 rows". Given a file of 5,000,000 rows in one row group, then a fast profile still reads about 100,000 rows, sampled within that row group.
- [x] AC-31 (US-03, this ticket's part: a Parquet file; a sheet's is DRIVN-68's): Given a sheet or a Parquet file that moved, was deleted or can't be read, when a run starts or fails partway, then the run ends `failed` with "DrivnX can't find this file. It may have moved or been deleted." or "DrivnX couldn't read this file.", and its results don't appear as current.

## Expected evidence
Integration tests: the rows a fast run reads for seed 42, twice; a 60,000-row file read whole; a 5,000,000-row, one-row-group file sampled to about 100,000; every row on a full run; a cancel 3 s into a full run of a 1 GB file, stopped within 5 s and `cancelled`; a deleted and an unreadable file ending `failed` with its message. The labels in Vitest. The performance targets for a 1 GB, 5-column file measured and recorded (fast at most 5 s, full at most 60 s).

## Notes
The ADR on how a Parquet file is sampled is written here; AC-25 checks it in DRIVN-72. The spec's open question on one huge row group is answered here.

## Blocked by
DRIVN-62.

## Verification (2026-11-26, on `feat/drivn-63-parquet-profile` from `e4765c4`, uncommitted)
| Criterion | Evidence | Result |
|---|---|---|
| AC-12: a fast profile reads about 100,000 rows from row groups chosen with the run's seed | `test_parquet_profile.py::test_a_fast_profile_reads_about_100000_rows_as_a_sample` (100,000 rows, scope `sample`; failed first with the interim 409). `test_the_same_seed_reads_the_same_rows_spread_over_the_file`: seed 42 twice gives the same 8 slices of 12,500 rows, one in each eighth, and the same missing count of a column whose gaps depend on the rows read; seed 43 differs. It failed first with no slices recorded. `test_a_column_named_like_duckdbs_row_number_is_sampled_as_any_other` (failed first: the run failed) | PASS |
| AC-12: labelled "about 100,000 rows, sampled by row group", and the row count "from the file's metadata" | `test_the_row_count_comes_from_the_files_metadata[fast, full]`: `row_count` with the scope `metadata` (failed first). `ParquetDetails.test.tsx`: "This file has 40,000,000 rows, from the file's metadata." and each column "about 100,000 rows, sampled by row group" (failed first) | PASS |
| AC-12: a second fast run with the seed 42 reads the same rows | as above: the same slices and the same metrics | PASS |
| AC-12: a full profile reads all the rows | `test_a_full_profile_reads_every_row_with_patterns_from_the_seeded_sample`: every row, scope `full`, copies counted, masked patterns from the fast run's slices with `sample_rows_read` (failed first). Vitest "every row, 40,000,000 rows", and patterns "from about 100,000 rows, sampled by row group" | PASS |
| AC-13 (a Parquet file's part): a cancel 3 s into a full profile of a 1 GB file stops within 5 s, `cancelled`, no current results | `test_cancelling_a_full_profile_stops_its_read_and_it_ends_cancelled`: 5,000,000 rows, cancelled once the read of every row has started: `cancelled`, no metrics, DuckDB interrupted. It passed at once (the CSV's cancel path), and failed when the job's connection wasn't registered. `tests/measure_large_parquet.py` on a 1.02 GB file through the UI: cancel 3 s in, stopped in 1.401 to 1.421 s over 3 runs | PASS |
| AC-30: a 60,000-row file is read whole, "every row, 60,000 rows"; 5,000,000 rows in one row group are sampled within it | `test_a_file_no_larger_than_the_sample_is_read_whole`, `test_one_huge_row_group_is_sampled_inside_it` (one row group checked; 100,000 rows from 8 slices). Both passed at once, as the slices don't depend on row groups. Vitest "every row, 60,000 rows" | PASS |
| AC-31 (a Parquet file's part): a file that moved, was deleted or can't be read | At the start: refused with "DrivnX can't find this file. It may have moved or been deleted." or "This file isn't a Parquet file…", and no run (`test_a_file_that_has_gone_is_refused_before_a_run_starts`, `…replaced_by_text…`; both failed first with a crash), as a CSV's start is (DRIVN-9/AC-11; confirmed with the user on 2026-11-26). Partway: `failed`, with "DrivnX can't find this file…", "DrivnX doesn't have permission to read this file." (the CSV run's wording, kept by the user's choice on 2026-11-26), or, for a value DuckDB can't read in a file whose footer still reads, "DrivnX couldn't read this file.", and no metrics (`test_a_file_removed_during_a_run_fails_and_says_so`, `test_a_file_that_cant_be_read_during_a_run_fails_and_says_so`, passed at once through the CSV's run; `test_a_damaged_value_fails_the_run_as_a_file_that_cant_be_read[fast, full]`, failed first with "DrivnX couldn't profile this file."). A file whose columns or types changed: `failed`, "The file's columns no longer match the saved ones.", no metrics (`test_a_file_whose_columns_changed_fails_rather_than_saving_wrong_evidence`, 2 cases; failed first: the run completed and saved the new columns' metrics as the old ones') | PASS |
| Expected evidence: the performance targets for a 1 GB, 5-column file, measured and recorded | `tests/measure_large_parquet.py`, 3 runs on the NVMe disk, through the UI: connect 0.216 to 0.252 s, fast 1.352 to 1.365 s (target 5 s), full 15.7 to 32.2 s (target 60 s); recorded in ADR 0010 | PASS |
| Notes: the ADR on how a Parquet file is sampled; the open question on one huge row group | `docs/adr/0010-parquet-sampling.md`, Decisions 1 to 7, with the measurements | PASS |

Decided with the user on 2026-11-26: the sample is 8 slices of 12,500 rows, one in each eighth of the file, from the
run's seed, kept in the run's scope; a full profile's masked patterns come from those slices.

Checks run, on the tree above after the review's fixes (run started 16:03:10; no code changed since):
- `uv run --frozen pytest tests/integration tests/golden`: 2692 passed, 8 skipped (the same platform and by-design
  skips; the PostgreSQL fixture was up), exit 0, in 9 min 13 s.
- `uv run --frozen pytest tests/e2e`: 99 passed, exit 0, in 3 min 56 s.
- `pnpm --dir apps/web exec tsc --noEmit`: exit 0. `pnpm --dir apps/web test`: 269 passed, 4 runs of 4 (`apps/web` unchanged by the review's fixes).
- `ai-skills knowledge check --stamp`: the DuckDB note re-checked and stamped; only `index/graph.yaml` out of date.

Not verified:
- `events.parquet` itself (40,000,000 rows in 326 row groups, written by Arrow): tests use DuckDB-written files of up to
  5,000,000 rows, and the measurement 24,705,359 rows.
- A finding on a Parquet run says it was observed "in the first 100,000 rows", the CSV's wording; findings are
  DRIVN-64's (AC-14).
- Reading a Parquet file's columns again once they've really changed: a follow-up ticket (review finding 5).
- CI on GitHub, macOS and Windows.

## Review (2026-11-26, the reviewer agent, on the uncommitted diff)
Verdict "ready after fixes"; AC-12, AC-13, AC-30 met, AC-31 met once its wording was settled. Each finding was
reproduced here first:
1. Risk: "Show values" on a Parquet run reached the CSV values route and crashed (500, `AttributeError`). Now refused
   with 409 "This Parquet file's values can't be worked out yet." until DRIVN-64
   (`test_a_parquet_runs_values_are_refused_until_they_can_be_worked_out`, failed first).
2. Risk (spec): AC-31 names "DrivnX couldn't read this file." for a file that can't be read. A damaged value now says
   that (it said "DrivnX couldn't profile this file."); a file DrivnX may not read keeps the CSV run's "DrivnX doesn't
   have permission to read this file.", both by the user's choice.
3. Nit: measuring keeps at most 100,000 rows, so a slice read too long went unseen. `test_a_sample_loads_exactly_the_rows_of_its_slices`
   fails if the slice's upper bound is one row too far.
4. Nit: `test_a_file_of_100000_rows_is_read_whole_and_one_more_is_sampled` (3 seeds) fails with `<` for `<=`.
5. Question: once a Parquet file's columns really change, its runs fail with "The file's columns no longer match the
   saved ones." and nothing lets the user read them again (no settings, and connecting the same path opens the old
   source). No criterion covers it; a follow-up ticket, by the user's choice on 2026-11-26.
6. Nit, not taken: `state: ready` while being built. Ticket files go `ready` → `done`; "being built" is Plane's In
   Progress, where DRIVN-63 is.

Confirmed sound by the reviewer: the slices over 200 seeds and row counts up to 2^63−1; `file_row_number` on a file
written with 8 threads and uneven row groups; the filter skipping row groups (0.107 s against 2.80 s on 1.09 GB); only
ints in the SQL; no false columns mismatch on 20 types; the CSV path unchanged by the refactor; no statistic read.

