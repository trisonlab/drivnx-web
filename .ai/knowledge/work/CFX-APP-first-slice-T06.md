---
id: CFX-APP-first-slice-T06
title: "Page through the rows"
feature: CFX-APP-first-slice
feature_revision: 4
stories: [US-06]
criteria: [AC-27, AC-28, AC-29, AC-30, AC-31]
blocked_by: [CFX-APP-first-slice-T05]
state: ready
external_key: null
status: CONFIRMED
---

# Page through the rows

## Outcome
The user moves through a CSV with next and previous. Cells display as plain text, long cells are cut and marked, and nothing is left behind.

## Acceptance checklist
- [ ] AC-27 (US-06): Given a connected CSV, when the preview opens, then the first 50 rows appear, and next and previous show the adjacent pages, with no total page count. Next is disabled on the last page.
- [ ] AC-28 (US-06): Given a request for more than 200 rows per page, then the API refuses or caps it to 200.
- [ ] AC-29 (US-06): Given preview cells containing HTML or script text, then they are displayed as plain text and nothing runs. Given a cell longer than 1,000 characters, then it is cut and visibly marked.
- [ ] AC-30 (US-06): Given preview pages have been viewed, then no row values are in `workspace.db` or its backups, and no files remain in `duckdb-tmp/` or the current directory after the job finishes.
- [ ] AC-31 (US-06): Given a file changes or disappears between pages, when the next page is requested, then the user gets current data or a clear error, and never a crash.

## Expected evidence
Tests naming each criterion, including script text in a cell, a file changed between pages, and a scan showing no row values remain.

## From T02
- AC-12 is only proven for the session token and launch code so far, because previews don't exist yet. Extend `test_logs_contain_no_token_or_launch_code` here to run a preview at `trace` level and check that no preview row values appear in the logs.

## From T05
- A connected file can have rows that don't fit, because detection now accepts one uneven row (`extra_field_in_a_row.csv`, `short_row.csv`). A strict read of those files fails at that row, so the preview needs a clear rule for them, tested against both files (AC-31).
- The source stores delimiter, encoding and header setting, but not how many lines to skip. `title_line.csv` has a line above its header, so check the preview still finds the right columns.
- Open files for the preview the way `scan_csv` does. `_readable_path` decides how each file has to be read: a little-endian UTF-16 file with characters above U+FFFF fails in DuckDB unless it's decoded first. Pass `compression = 'none'` too, or a plain file named `.gz` or `.zst` won't open.
