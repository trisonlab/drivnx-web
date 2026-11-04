---
id: CFX-APP-first-slice-T06
title: "Page through the rows"
feature: CFX-APP-first-slice
feature_revision: 8
stories: [US-06]
criteria: [AC-27, AC-28, AC-29, AC-30, AC-31]
blocked_by: [CFX-APP-first-slice-T05]
state: done
external_key: null
status: CONFIRMED
---

# Page through the rows

## Outcome
The user moves through a CSV with next and previous. Cells display as plain text, long cells are cut and marked, and nothing is left behind.

## Acceptance checklist
- [x] AC-27 (US-06): Given a connected CSV, when the preview opens, then the first 50 rows appear, and next and previous show the adjacent pages, with no total page count. Next is disabled on the last page. Given lines above the table that detection skipped, the preview says how many aren't shown. Correcting this comes later in M2. Given a malformed file whose first double quote comes after the rows detection samples and never closes, the rows after that quote may not appear. Given a file with mixed line endings near its start, a field beyond the expected columns in a later row may not be shown.
- [x] AC-28 (US-06): Given a request for more than 200 rows per page, then the API refuses or caps it to 200.
- [x] AC-29 (US-06): Given preview cells containing HTML or script text, then they are displayed as plain text and nothing runs. Given a cell or column name longer than 1,000 characters, then it is cut and visibly marked.
- [x] AC-30 (US-06): Given preview pages have been viewed, then no row values are in `workspace.db` or its backups, the browser's disk cache, or any files remaining in `duckdb-tmp/` or the current directory after the job finishes.
- [x] AC-31 (US-06): Given a file changes or disappears between pages, when the next page is requested, then the user gets current data or a clear error, and never a crash.

## Expected evidence
Tests naming each criterion, including script text in a cell, a file changed between pages, and a scan showing no row values remain.

## From T02
- AC-12 is only proven for the session token and launch code so far, because previews don't exist yet. Extend `test_logs_contain_no_token_or_launch_code` here to run a preview at `trace` level and check that no preview row values appear in the logs.

## From T05
- A connected file can have rows that don't fit, because detection now accepts one uneven row (`extra_field_in_a_row.csv`, `short_row.csv`). A strict read of those files fails at that row, so the preview needs a clear rule for them, tested against both files (AC-31).
- The source stores delimiter, encoding and header setting, but not how many lines to skip. `title_line.csv` has a line above its header, so check the preview still finds the right columns.
- Open files for the preview the way `scan_csv` does. `_readable_path` decides how each file has to be read: a little-endian UTF-16 file with characters above U+FFFF fails in DuckDB unless it's decoded first. Pass `compression = 'none'` too, or a plain file named `.gz` or `.zst` won't open.

## Decisions in building this
- **Rows that don't fit (AC-31):** a short row gets blanks, and an extra field in the sample gets its own column, which the preview's columns then show. A row that still can't fit gives a clear error for every page after it, and for up to about 2,000 rows before it, because DuckDB fails a whole batch of rows at once. Lines above the table can be skipped, and the preview says how many (AC-27).
- **A file that changed (AC-31):** every page reads the file again, using the delimiter and header setting saved when it was connected. If its size or modified time has changed, the encoding is detected again, which reads the whole file once for each page. An unchanged file skips that and stays fast. The preview takes its columns from the read, so they can differ from the saved ones. Errors the file causes are a 409 with a message for the user.
- **A title line with an uneven row:** `Sales report`, then a header, then a short row, is saved with `Sales report` as its only column. The preview then shows the real header as its first row. Nothing is hidden, and this falls under the limit in AC-22.

- **Reading each page:** comment detection is off, because it removed rows starting with `#`. The parallel reader is off, because it could invent a row and shift the rest. If the sample shows no quotes, the standard double quote is tried first, so a quoted value further on is still read as one. Cells are cut in the SQL, so huge ones never reach Python.
- **Mixed line endings:** the lax read is only used when no other sniff can read the file, and the limit it leaves is in AC-27. `test_mixed_line_endings_and_a_late_extra_field_lose_that_field` pins it. A short row late in such a file gives a clear error.
- **Known limit, the last moment:** a file that becomes a FIFO after Conflux's own checks could still block inside DuckDB's own open, which Conflux can't guard.

## Measured
1 GB synthetic file, freshly written to tmpfs, so these are not cold-disk figures. Connect takes 2.06 s for UTF-8 and 1.80 s for Latin-1, against a budget of 5 s. The first page takes 0.14 s and 0.30 s, against a budget of 2 s. Later UTF-8 pages: 3.42 s at row 10 million, and 6.98 s at row 22 million, near the end, with memory peaking at about 1.1 GB. Measured 2026-11-03.
