---
id: CFX-APP-csv-profile-T07
title: "Runs that can't go ahead"
feature: CFX-APP-csv-profile
feature_revision: 2
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
- From T04: a job's guard accepts extra columns after the saved ones, because a wider row reads that way (`extra_field_in_a_row.csv`). AC-12's check must not refuse those files, while still telling them apart from a file with a real new column.
- From T04: `load_rows` turns every DuckDB error into "a row can't be read", including running out of memory, so a failure message mustn't assume the file is at fault. `POST /api/profile-runs` already refuses a file that has gone, with no test yet for one that can't be read.
- From T04, decided by the user: one row wider than the table inside the first rows, or just after them, fails a run, as in the preview. Skipping or cutting it would make the counts quietly wrong. Today the screen only says "This profile couldn't finish", so it needs a clear message here.
- From reviewing T05, older than T05: a late unterminated quote silently loses rows. With 25,000 rows, then `2,"oops`, then more rows (30,002 in all), a run completes with 25,000 rows read and is labelled as the whole file, and the preview stops at the same row. DuckDB raises no error, so the next way to read is never tried: its standard quote reads 25,000 rows, and with no quote option and padding it reads 30,002. A 4-line file with the same quote reads correctly, so the threshold isn't known. By the user's "fail, never cut" decision this should fail, not report a short count. One option is to compare the counts from both reads and refuse if they differ, at the cost of a second read.
- From verifying T05, older than T05 (INFERRED: that read path didn't change): a 41-line file whose last row has one extra field reads as 1 row, because DuckDB takes the 40 lines above it as lines to skip and the wider row as the table. The same decision applies.
- From reviewing T05: connect `a,b`, then rewrite the file as `a,b,secret`. The run completes with `extra_columns` 1, and the Profile tab says "some rows" have an extra field, when every row does. The name `secret` isn't saved. AC-12's check needs to tell this apart from a wider row.
