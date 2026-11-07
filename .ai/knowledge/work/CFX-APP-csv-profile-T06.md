---
id: CFX-APP-csv-profile-T06
title: "Full profile and cancel"
feature: CFX-APP-csv-profile
feature_revision: 4
stories: [US-02, US-03]
criteria: [AC-08, AC-10, AC-18]
blocked_by: [CFX-APP-csv-profile-T05]
state: done
external_key: null
status: CONFIRMED
---

# Full profile and cancel

## Outcome
A user can run a whole-file profile with exact results and a row count, and can cancel any run in progress.

## Acceptance checklist
- [x] AC-08 (US-02): Given a connected CSV, when the user chooses Full profile, then a run reads every row and completes with exact results and the file's row count.
- [x] AC-10 (US-02): Given a run in progress, when the user cancels it, then it stops within the target under Non-functional, its status is `cancelled`, and no results or findings from it appear as current.
- [x] AC-18 (US-03): Given a full profile, then its masked patterns are labelled as coming from the first 100,000 rows, and its other metrics as the whole file.

## Expected evidence
Tests naming each criterion, and the cancel time measured on a large file.

## From planning
- Test a cancel arriving just as a run finishes. A run's status can only change once.
- From T04: pressing Ctrl+C during a run exits cleanly and the next start marks it `interrupted`, but DuckDB's binding prints "leaked function" warnings, as the job's thread still holds a connection. No rows or names were in the output. Fast runs are short, and a full profile makes this much more likely, so stopping a job should also cover shutting down.
- From planning T07: DuckDB 2.0.0 silently drops every row after an unterminated quote that comes after the first 2,046 rows. T07 catches this in a fast run with a lax count of the same read when the run would claim the whole file. A full profile always claims the whole file, so it needs the same check across all of it, and its cost on a large file needs measuring.

## What planning measured
On 2026-11-07, a 1 GB, 5-column synthetic file of 26,120,000 rows, read through `load_rows` with DuckDB limited to 4 GB, on a RAM disk, so the times flatter. T12 measures the real product.
- **A whole-file read and measure is well within the 60 s target.** Copying every row into a table took 9.07 s, including 2.65 s for the lax count that catches a late unterminated quote. Measuring the 5 columns took 5.31 s. Memory peaked at 2.94 GB.
- **An interrupt stops a whole-file read within 0.001 to 0.006 s,** 0.5, 1.5 and 4 s into it, but `load_rows` swallowed it: DuckDB's `InterruptException` is a `duckdb.Error`, so the next way of reading was tried and the read finished.
- **An interrupt while no query runs is lost,** and the next query runs normally. Interrupting a connection that has closed raises `ConnectionException`.

## Plan agreed with the user
Decided by the user, 2026-11-07, taking every recommendation:
- **A full profile copies the whole file into a table, as a fast profile copies its first rows, then measures every row.** It reuses the ways of reading, formats, extra-columns note and late-quote check, reads the file once, and leaves T08's duplicate count as one more query. Rejected: one read of the file per column (slow on wide files), and one streaming pass over every column (a new query shape, and T08 would need another full read).
- **The job marks a run `cancelled`, not the cancel route,** so a run's status changes once (ADR 0002) and no DuckDB work continues behind a run that looks finished. The route answers 202.
- **A cancel sets a stop flag and interrupts the job's connection every 50 ms until the job ends,** and `load_rows` lets `InterruptException` through, as it does running out of memory. One lock covers the job's choice of `completed` or `cancelled` and the route's choice of 202 or 409, so a cancel arriving as a run finishes gives either 202 and `cancelled` or 409 and `completed`.
- **Shutting down stops running jobs** and waits up to 5 s for them, leaving the run `running`, so the next start marks it `interrupted`.
- **The tab says how many rows the file has** for every run that read the whole file.
- **Masked patterns are saved as `sample` when the file has more than 100,000 rows,** and the Patterns cell says "first 100,000 rows, not random" when that differs from the column's other metrics.
- **The cancel button is on the profiled file's tab** while its run is going.

Build order:
0. Rename `measure_first_rows` to `measure_rows(connection, table, types, formats, every_row: bool)` and `FirstRows` to `MeasuredRows`, with no change in behaviour.
1. AC-08 in the API: `mode: "full"`, recorded with no sample size or strategy, reading with `load_rows(…, limit=None)`. Tested with missing values planted after row 100,000, and a late quote after it.
2. AC-18: the patterns' scope, and its label on the tab.
3. AC-08 on screen: the Full profile button and the row count.
4. AC-10: `POST /api/profile-runs/{id}/cancel` (202, 404, 409 "This profile has already finished."), tested during a read of a file of about 200 MB, between two queries, just as a run finishes, and with a run started after it.
5. AC-10 on screen: the Cancel profile button, "This profile was cancelled.", and the note on results from the run before.
6. Shutting down during a run.
7. The full profile and cancel times on the 1 GB file through the API, the full suites, and a real-browser probe.

Known limits from planning:
- A full profile of a file larger than the memory limit spills about its size into the workspace's `duckdb-tmp`, holding source data for the job's length (R4, accepted in ADR 0004).
- A very wide file takes a while to profile in full, though cancelling stops it at once.

## What building it showed
- **A full profile reuses the fast path.** `load_rows(…, limit=None)` reads every row, which always runs T07's lax count, so a quote never closed after row 100,000 fails a full run with the unreadable-row reason. The job's table is now `loaded_rows`, and the start route passes the mode through to `start_profile`.
- **Patterns keep their own scope.** On a full profile of more than 100,000 rows they're saved as `sample`, and the Patterns cell says "from the first 100,000 rows, not random". The run's other metrics say "whole file".
- **A cancel is caught wherever it lands.** Tests cancel during the read, between the read and measuring, while saving observations, and just as the run finishes. A single interrupt sent before the read's first query was lost every time, and the read ran to the end. `load_rows` swallowing the interrupt turned a cancel into an unreadable row. A cancel while saving stops before the next batch. A cancel accepted as a run finishes is what the run ends as, as the job decides its ending under the lock that a cancel takes.
- **Stopping Conflux during a run now exits cleanly.** Without stopping the job on shutdown, Ctrl+C during a full profile ended the process with code -6 (an abort), not only the warnings T04 saw. Now it exits with 0 and prints no "leaked" warning, and the run is marked `interrupted` at the next start.
- **Measured on the 1 GB file through the API,** on a RAM disk, three times each: a full profile took 15.52 to 15.89 s for 26,120,000 rows, and a cancel 2 s into a run ended it as `cancelled` in 0.019 to 0.020 s. The targets are 60 s and 5 s. T12 measures the real product on a disk.
- **Known limit:** the cancel route waits for the lock while a job records its ending. That write waits up to the busy timeout if another program holds the workspace's write lock, so a cancel can take that long to answer.

## From reviewing T06
- **A run whose ending couldn't be recorded kept being interrupted.** When `finish_profile_run` raised, as it does if another program holds the workspace past the busy timeout, the job never set `ended`, so a cancel's interrupting thread ran on forever. It's now set in the same `finally` that removes the job. A test makes recording the ending fail and checks no interrupting thread is left. The run itself still stays `running` until the next start, as recorded for T07, and cancelling it again answers "This profile has already finished."
- **A cancel just after a run was saved was refused.** The run could be seen before its job was known, so a cancel in that moment got 409 while the run completed. The run is now saved and its job registered under the lock that a cancel takes, in the same order as the job's ending: `_jobs_lock`, then SQLite's write lock.
- **The AC-18 test didn't prove where patterns come from.** It checked only their scope, and still passed with patterns taken from every row. It now checks the count: `x` 99,997 times in the first 100,000 rows, not 149,991.
- **The test of a cancel as a run finishes always meets the same order,** as the run has chosen `completed` while it holds the lock, so it now asserts 409 and `completed` exactly. The saving test covers a cancel that wins.
- **Pre-existing, older than T06:** a UTF-16 file is decoded in Python before DuckDB reads it, and an interrupt can't stop that step, in a fast profile or a full one.

## Verification
Verified 2026-11-07 on the uncommitted branch `feat/cfx-app-csv-profile-t06-full-profile-and-cancel`, after the review's fixes.

| Criterion | Evidence | Result |
|---|---|---|
| AC-08 | `test_a_full_profile_reads_every_row_and_counts_exactly` (150,001 rows, 10 missing values counted, 7 of them after row 100,000), `test_a_full_run_is_recorded_as_reading_every_row`, `test_a_quote_never_closed_after_the_first_rows_fails_a_full_run`, the tab's Vitest tests for the Full profile button and "This file has N rows.", and the Playwright test `test_a_full_profile_counts_every_row_and_labels_its_patterns`, all passing. A real browser showed "This file has 150,001 rows." | PASS |
| AC-10 | `test_cancelling_a_run_stops_its_read_and_it_ends_cancelled` (within 5 s, no results, the read stopped by an interrupt, and a new run starts after it), `test_a_cancel_between_two_queries_still_stops_the_run`, `test_a_cancel_while_saving_saves_no_more`, `test_a_cancel_arriving_as_a_run_finishes_is_refused`, `test_a_cancel_as_a_run_starts_waits_for_its_job`, `test_a_cancelled_run_that_cant_record_its_end_still_stops_interrupting`, `test_a_run_that_has_ended_or_doesnt_exist_cant_be_cancelled`, the tab's Vitest tests, and the Playwright test `test_a_full_profile_can_be_cancelled`, all passing. On the 1 GB file through the API, a cancel ended a run in 0.019 to 0.020 s. Findings come in T08. | PASS |
| AC-18 | `test_a_full_profile_takes_patterns_from_the_first_rows_only` (scope `sample`, and `x` counted 99,997 times, not 149,991; seen failing with patterns taken from every row), the Vitest test for the patterns' label, and the Playwright test, all passing. A real browser showed "1 pattern, from the first 100,000 rows, not random" in a row labelled "whole file". | PASS |

Checks run: `uv run --frozen pytest tests --ignore=tests/e2e` (2067 passed, 6 skipped: files that can't be connected, by design), `uv run --frozen pytest tests/e2e` (62 passed), `pnpm --dir apps/web test` (112 passed), `pnpm --dir apps/web build` (type-check and build, exit 0), `uvx ruff check packages apps tests testdata` (all passed), `git diff --check` (clean), `ai-skills knowledge check` (consistent). The suites started after the last code change.

Not verified: CI, as there's no remote yet. The 1 GB times, and the real-browser probe, are from before the review's fixes, which only changed when a job is registered and when it's marked ended; the Playwright tests of a full profile and a cancel ran after them. The 1 GB times are from a RAM disk, so they flatter; T12 measures the real product on a disk.
