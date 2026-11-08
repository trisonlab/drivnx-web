---
id: DRIVN-27
legacy_id: CFX-APP-csv-profile-T07
title: "Runs that can't go ahead"
feature: CFX-APP-csv-profile
feature_revision: 4
stories: [US-01, US-02]
criteria: [AC-06, AC-11, AC-12]
blocked_by: [DRIVN-24]
state: done
external_key: DRIVN-27
status: CONFIRMED
---

# Runs that can't go ahead

Plane: [DRIVN-27](https://app.plane.so/drivnx/browse/DRIVN-27/) · was `CFX-APP-csv-profile-T07`

## Outcome
A file that's gone, unreadable or changed, or a correction attempted during a run, gives a clear message and never leaves misleading results.

## Acceptance checklist
- [x] AC-06 (US-01): Given a profile run in progress, when the user tries to save a correction, then it is refused with a clear message.
- [x] AC-11 (US-02): Given a file that has moved, was deleted or can't be read, when a run starts or fails partway, then the user gets a clear message, a run that started ends as `failed`, and its results don't appear as current.
- [x] AC-12 (US-02): Given a file whose columns no longer match the saved ones, when a run starts, then it is refused with a message pointing to the source's settings, and saving those settings reads the columns again.

## Expected evidence
Tests naming each criterion, including a file removed during a run and a file that changes during a run.

## From planning
- DuckDB's error messages quote rows, so a failed run must not save or log them. The preview already avoids this.
- From T04: a job's guard accepts extra columns after the saved ones, because a wider row reads that way (`extra_field_in_a_row.csv`). AC-12's check must not refuse those files, while still telling them apart from a file with a real new column.
- From T04: `load_rows` turns every DuckDB error into "a row can't be read", including running out of memory, so a failure message mustn't assume the file is at fault. `POST /api/profile-runs` already refuses a file that has gone, with no test yet for one that can't be read.
- From T04, decided by the user: one row wider than the table inside the first rows, or just after them, fails a run, as in the preview. Skipping or cutting it would make the counts quietly wrong. Today the screen only says "This profile couldn't finish", so it needs a clear message here.
- From reviewing T05, older than T05: a late unterminated quote silently loses rows. With 25,000 rows, then `2,"oops`, then more rows (30,002 in all), a run completes with 25,000 rows read and is labelled as the whole file, and the preview stops at the same row. DuckDB raises no error, so the next way to read is never tried: its standard quote reads 25,000 rows, and with no quote option and padding it reads 30,002. A 4-line file with the same quote reads correctly, so the threshold isn't known. By the user's "fail, never cut" decision this should fail, not report a short count. One option is to compare the counts from both reads and refuse if they differ, at the cost of a second read.
- From verifying T05, older than T05 (INFERRED: that read path didn't change): a 41-line file whose last row has one extra field reads as 1 row, because DuckDB takes the 40 lines above it as lines to skip and the wider row as the table. The same decision applies.
- From reviewing T05: connect `a,b`, then rewrite the file as `a,b,secret`. The run completes with `extra_columns` 1, and the Profile tab says "some rows" have an extra field, when every row does. The name `secret` isn't saved. AC-12's check needs to tell this apart from a wider row.

## What planning measured
- **A late unterminated quote is a DuckDB 2.0.0 bug with a clear threshold.** Within the first 2,046 rows DuckDB raises an error. From row 2,047 on, the rows from the quote onwards disappear without one. No read option makes it raise: not ignoring errors, saving rejects, a larger buffer or a full sample. A lax read (`strict_mode = false`) counts one row more, so the counts disagree.
- **The counts agree on healthy files.** On every testdata and golden file, with both header settings, a lax count matched the first read in all 78 cases, keeping padding where that read used it. Without padding, the two files with uneven rows can't be read laxly.
- **A sniff with the saved settings tells a new column from a wider row.** Strict, then lenient: `extra_field_in_a_row.csv` gives its 3 saved names while the padded read finds a 4th, `a,b` rewritten as `a,b,secret` gives all 3 names, and a file with no header rewritten to 3 columns gives 3.
- **The wider last row is a skip, not a lost row.** Connecting the 41-line file skips 40 lines and saves 3 columns, and the preview says 40 lines were skipped. The first slice decided that the preview skips lines above the table and says how many.

## Plan agreed with the user
Decided by the user, 2026-11-06, taking every recommendation:
- **A failed run's reason is kept in memory,** by run ID, so after a restart the tab falls back to "This profile couldn't finish". Saving it would need a migration, a change to ADR 0002 and a revision of the first slice's AC-42.
- **AC-06 refuses only corrections to the file being profiled.** The check is inside the write transaction that saves them. A run also can't start on a dataset that a correction has just replaced.
- **Rows lost after a late quote fail the run.** When a run would claim the whole file, a lax count of the same read, capped at the limit, must agree. The preview keeps the first slice's AC-27 limit for malformed files.
- **Lines left out above the table are saved as `skipped_lines`** and shown on the tab, as the preview shows them. The user can correct them by choosing to skip 0 lines.
- **Saving settings when the workspace is busy answers 503** with the busy message, fixing T03's note, as AC-06 changes the same route.

Build order:
1. AC-06: refuse a correction during a run on the same file, and a start on a replaced dataset.
2. AC-11 partway: the job checks the file after any failure (gone, no permission, changed in size, modified time, inode or device), and running out of memory is told apart from an unreadable row. The reason is never taken from DuckDB's text. The run's response has `failure`, and the tab shows it, saying when results below come from the run before.
3. AC-11 at start: a test that a file that can't be read is refused, seen failing with that branch reverted.
4. AC-12: `sniffed_columns` reads the columns with the saved settings, and a start is refused unless they are the start of the saved ones. A committed test checks no unchanged testdata file is refused.
5. The late-quote check in `load_rows`, with the extra read measured on a 100,000-row file.
6. `skipped_lines` saved and shown.
7. The full suites, the testdata sweep, and a real-browser probe of the refusal, a failure message and the skipped-lines note.

Known limits from planning:
- A file that can only be read laxly (mixed line endings) and also has a late quote merges the rest of the file into one value. Both counts agree, so this check can't see it. It belongs with the first slice's AC-27 limit for malformed files.
- A path replaced by a folder or a named pipe still says the file can't be found.

## What building it showed
- **The plan's column check was too loose.** Accepting columns that are the start of the saved ones would also accept a removed column. Columns are now compared exactly, read the way they were saved: a sniff with the saved settings for a source never corrected, and the preview's read of the first row for a corrected one, as saving settings takes the preview's columns, a wider row's extra column included. A sniff that can't read the file lets the run go ahead, and the job's own check stays.
- **A file that can't be read wasn't refused at the start.** The test written to prove the existing branch passed with it removed: the start doesn't read a file whose size and modified time are unchanged, and `chmod` changes neither. The column check now opens the file, so it's refused.
- **The busy 500 on saving settings was already fixed** by T04's handler for the whole app. A test now holds it, seen failing with the handler removed.
- **Finding a reason must not open a pipe.** A file replaced by a named pipe made the reason check wait for a writer forever, so the run never ended and blocked every later one. The file's identity is now compared before it's opened.
- **Running out of memory isn't blamed on the file.** `load_rows` lets DuckDB's out-of-memory error through instead of trying the next way of reading. Its path for uneven lines still reports it as an unreadable row. No test reaches it, as memory runs out on the first way of reading first.
- **A late quote fails with the unreadable-row reason,** not a sentence of its own, as the row with the quote is the one that can't be read. The lax count added 0.07 to 0.16 s to loading a 4 MB file of 100,000 rows (0.23 to 0.25 s without it, 0.31 to 0.41 s with it, three runs each). If the lax read itself raised an error, the next way of reading would be tried as before; on the 78 files measured in planning it never did, so nothing handles it.

## From reviewing T07
- **A correction could slip in while a run started.** The start read the source's settings, then the file, and only then the dataset, so a correction saved in between gave a completed run, shown as current, that read with the old settings and counted a header as a row. The start now reads the dataset first, so a correction after either read replaces it and the start is refused. A test saves the correction while the start reads the file's columns.
- **The busy message didn't reach the screen.** Saving settings answered 503 with "DrivnX is busy… Try again.", but the form said "Start DrivnX again". It now shows the server's words, as starting a run already did.
- **Known limit, older than T07 (INFERRED from reading the code, not reproduced):** if the workspace is busy past the timeout just as a job records its end, the run stays `running` until DrivnX restarts, and every later start is refused until then. The job records its end in `finally`, so only that write can fail this way.

## Verification
Verified 2026-11-07 on the uncommitted branch `feat/cfx-app-csv-profile-t07-runs-that-cant-go-ahead`, after the review's fixes.

| Criterion | Evidence | Result |
|---|---|---|
| AC-06 | `test_a_correction_during_a_run_on_the_same_file_is_refused`, `test_a_correction_to_another_file_during_a_run_is_saved`, `test_a_start_that_meets_a_correction_is_refused`, `test_a_correction_while_a_start_reads_the_file_is_refused` (seen failing with 202 before the review's fix), `test_a_run_cant_start_on_settings_a_correction_just_replaced`, all passing. A real browser on the installed wheel showed "A profile is running on this file. Wait for it to finish, then save your settings." | PASS |
| AC-11 | Tests for a file gone at the start, unreadable at the start, removed, changed, made unreadable, replaced by a pipe or unable to be looked at during a run, a busy workspace, running out of memory, an unreadable row and a late quote, each ending `failed` with no metrics and a reason. The tab's Vitest tests show the reason and label earlier results as the run before. A real browser showed "This profile couldn't finish. A row in this file can't be read, so it can't be profiled without leaving that row out." | PASS |
| AC-12 | `test_a_file_whose_columns_changed_is_refused_before_a_run_starts` (10 cases), `test_saving_the_settings_after_a_refusal_reads_the_columns_again`, `test_every_file_that_can_be_connected_can_be_profiled` and `test_every_file_can_be_profiled_after_saving_its_settings`, all passing. A real browser showed the message pointing to the source's settings. | PASS |

Checks run: `uv run --frozen pytest tests --ignore=tests/e2e` (2054 passed, 6 skipped: files that can't be connected, by design), `uv run --frozen pytest tests/e2e` (60 passed, run after both review fixes; only a test changed since), `pnpm --dir apps/web test` (105 passed), `pnpm --dir apps/web build` (type-check and build, exit 0), `uvx ruff check packages apps tests testdata` (all passed), `git diff --check` (clean), a one-off browser probe of the four messages on a freshly built wheel (4 passed).

Not verified: CI, as there's no remote yet. The one-off sweep of every testdata file across header and skip settings (169 runs completed, none refused) ran before the review's fix to the start, which only changed the order of two reads; the committed every-file tests ran after it. The known limits above stay open.
