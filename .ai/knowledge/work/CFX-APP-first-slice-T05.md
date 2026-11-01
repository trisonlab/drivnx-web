---
id: CFX-APP-first-slice-T05
title: "Connect a CSV and see its metadata"
feature: CFX-APP-first-slice
feature_revision: 1
stories: [US-04, US-05, US-08]
criteria: [AC-20, AC-21, AC-22, AC-23, AC-24, AC-25, AC-26, AC-49]
blocked_by: [CFX-APP-first-slice-T04]
state: blocked
external_key: null
status: CONFIRMED
---

# Connect a CSV and see its metadata

## Outcome
The user pastes a path and sees the detection result, the file details and its columns. The file stays where it is, and no data row is saved. Start by proving detection against the synthetic files, because the assumed encodings are unconfirmed; if detection falls short, stop and raise a spec change.

## Acceptance checklist
- [ ] AC-20 (US-04): Given the path of a readable CSV, when the user connects it, then it appears as a source, and the file's contents and modified time are unchanged and no copy of it exists in the workspace.
- [ ] AC-21 (US-04): Given a path that doesn't exist, is a directory, can't be read, or isn't parseable as CSV, when the user connects it, then a specific error says which, and no source is added.
- [ ] AC-22 (US-04): Given each synthetic CSV covering the assumed delimiters, encodings and header cases, when it is connected, then the detected delimiter, encoding and header setting are correct and are shown to the user.
- [ ] AC-23 (US-04): Given a source whose file has since moved or been deleted, when the workspace is reopened, then the source is listed as unavailable and the app keeps working.
- [ ] AC-24 (US-05): Given a connected CSV, when its source is opened, then the Data Explorer shows it with its columns, and the app shows file name, size, modified date, and each column's name, detected type and original type text.
- [ ] AC-25 (US-05): Given a connected CSV, then no row count is shown, and every value from the scan is labelled as metadata.
- [ ] AC-26 (US-05): Given a connected CSV, then its Source, Dataset and Fields are in `workspace.db`, and a scan of the database finds no value from any data row.
- [ ] AC-49 (US-08): Given a DuckDB job, then it creates nothing in the current directory, its `temp_directory` is `<workspace>/duckdb-tmp/`, and leftovers there are removed at the next start.

## Expected evidence
Synthetic CSVs for each delimiter, encoding and header case. Tests naming each criterion, including a database scan for row values and a check that DuckDB writes nothing to the current directory.
