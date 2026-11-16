---
id: DRIVN-15
legacy_id: CFX-APP-first-slice-T05
title: "Connect a CSV and see its metadata"
feature: CFX-APP-first-slice
feature_revision: 10
stories: [US-04, US-05, US-08]
criteria: [AC-20, AC-21, AC-22, AC-23, AC-24, AC-25, AC-26, AC-49]
blocked_by: [DRIVN-14]
state: done
external_key: DRIVN-15
status: CONFIRMED
---

# Connect a CSV and see its metadata

Plane: [DRIVN-15](https://app.plane.so/drivnx/browse/DRIVN-15/) · was `CFX-APP-first-slice-T05`

## Outcome
The user pastes a path and sees the detection result, the file details and its columns. The file stays where it is, and no data row is saved. Start by proving detection against the synthetic files, because the assumed encodings are unconfirmed; if detection falls short, stop and raise a spec change.

## Acceptance checklist
- [x] AC-20 (US-04): Given the path of a readable CSV, when the user connects it, then it appears as a source, and the file's contents and modified time are unchanged and no copy of it exists in the workspace.
- [x] AC-21 (US-04): Given a path that doesn't exist, is a directory, can't be read, isn't parseable as CSV, is UTF-16 without a BOM, is a Windows-1252 or big-endian UTF-16 file over 100 MB, or is a little-endian UTF-16 file over 100 MB with characters above U+FFFF such as emoji, when the user connects it, then a specific error says which, and no source is added. The over-limit error says to save the file as UTF-8.
- [x] AC-22 (US-04): Given each synthetic CSV covering the assumed delimiters, encodings and header cases, when it is connected, then the detected delimiter, encoding and header setting are correct and are shown to the user. Given a file where detection can't tell a header from data, one row from the file may be used as column names, and the delimiter may be wrong. The screen shows the detected delimiter and says a row from the file was used as column names. Correcting either comes later in M2.
- [x] AC-23 (US-04): Given a source whose file has since moved or been deleted, when the workspace is reopened, then the source is listed as unavailable and the app keeps working.
- [x] AC-24 (US-05): Given a connected CSV, when its source is opened, then the Data Explorer shows it with its columns, and the app shows file name, size, modified date, and each column's name, detected type and original type text.
- [x] AC-25 (US-05): Given a connected CSV, then no row count is shown, and every value from the scan is labelled as metadata.
- [x] AC-26 (US-05): Given a connected CSV, then its Source, Dataset and Fields are in `workspace.db`, and a scan of the database finds no value from any data row. The one exception is the row used as column names (AC-22). No other row's values are stored.
- [x] AC-49 (US-08): Given a DuckDB job, then it creates nothing in the current directory, its `temp_directory` is `<workspace>/duckdb-tmp/`, and leftovers there are removed at the next start.

## Expected evidence
Synthetic CSVs for each delimiter, encoding and header case. Tests naming each criterion, including a database scan for row values and a check that DuckDB writes nothing to the current directory.

## From T04
- AC-15 is only proven for the name and AI mode so far. Extend `test_setup_opens_the_app_and_a_restart_keeps_it` to check that sources are kept too.
- AC-13 picks up new GET routes automatically through `test_get_and_head_routes_change_no_workspace_data`, but its data snapshot needs a source in it to mean anything here.
