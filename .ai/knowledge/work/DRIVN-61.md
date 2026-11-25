---
id: DRIVN-61
title: "Parquet: connect and read its metadata"
feature: DRIVN-60
feature_revision: 1
stories: [US-01, US-02]
criteria: [AC-07, AC-08, AC-09, AC-29]
blocked_by: []
state: done
external_key: DRIVN-61
status: CONFIRMED
---

# Parquet: connect and read its metadata

Plane: [DRIVN-61](https://app.plane.so/drivnx/browse/DRIVN-61/)

## Outcome
A user connects a Parquet file by its path and, within a second, sees its columns with their native and normalised types, its row count, its row groups and the program that wrote it, all read from its metadata without scanning a row. An encrypted file, a folder, a remote path and a file that isn't Parquet are refused with their messages, and nothing is saved. A file is read by what its content is, not its name.

## Acceptance checklist
- [x] AC-07 (US-02): Given `events.parquet`, when the user connects it, then within 1 s its columns with their native types, "40,000,000 rows", "326 row groups" and "Written by parquet-cpp-arrow version 17.0.0" are shown, read from its metadata without scanning a row. No response and no row of `workspace.db` holds a minimum or maximum from its statistics.
- [x] AC-08 (US-02): Given one of these paths, when the user connects it, then it's refused with its message and nothing is saved: an encrypted Parquet file, "This Parquet file is encrypted, so DrivnX can't read it."; a folder, "This path is a folder. Enter the path to a CSV, XLSX or Parquet file; a folder of Parquet files isn't supported yet."; `s3://bucket/events.parquet` or `https://example.com/events.parquet`, "DrivnX reads files on this computer only. Enter the full path to a file."; a file named `.parquet` that isn't one, or one cut short, "This file isn't a Parquet file, so it can't be read as one."
- [x] AC-09 (US-02): Given Parquet columns of the types `UBIGINT`, `DECIMAL(38,2)`, `TIMESTAMP_NS`, `TIMESTAMP WITH TIME ZONE`, `UUID`, `BLOB`, `INTEGER[]`, `STRUCT(a INTEGER)` and `MAP(VARCHAR, INTEGER)`, then each column shows its native type and its type in ADR 0003's vocabulary: integer, number, timestamp, timestamp, string, string, and string for the list, struct and map, which also say "list", "struct" and "map".
- [x] AC-29 (US-01, this ticket's part: CSV text and Parquet told apart by content; a workbook's is DRIVN-66's): Given a file named `orders.csv` whose content is an XLSX workbook, or `sales.xlsx` whose content is CSV text, then DrivnX reads it by what its content is, not its name, and the source says which it was read as.

## Expected evidence
Integration tests: metadata read with no row scanned and no statistics minimum or maximum in a response or in `workspace.db`; each type in AC-09 with its native and normalised type; each refusal and its message with nothing saved; a Parquet file named `.csv`, and CSV text named `.parquet`, read by content. The migration adding the `xlsx` and `parquet` source kinds, up and down. Vitest for the metadata view. e2e connecting a Parquet file against the installed wheel, with axe. Connecting a 40,000,000-row file timed against 1 s.

## Notes
The migration adds both new source kinds, `xlsx` and `parquet`, so DRIVN-66 needs none for that. `normalised_type` needs Parquet's types (AC-09), which it maps to `string` today.

## Blocked by
None, it can start now.

## Verification (2026-11-25, on `feat/drivn-61-parquet-metadata` from `633311d`, uncommitted)
| Criterion | Evidence | Result |
|---|---|---|
| AC-07: columns with native types, row count, row groups and writer, from the metadata | `test_parquet_sources.py::test_connecting_a_parquet_file_shows_its_metadata`: 10,240 rows in 5 row groups written by DuckDB, its writer string taken from DuckDB's own `pragma_version()`; listed with the same values. Seen failing with 422 while every file was read as a CSV | PASS |
| AC-07: within 1 s, without scanning a row | `test_connecting_40_million_rows_takes_at_most_1_second`: 40,000,000 rows in 326 row groups (159 MB) connected in 0.048 s. The reader runs only `parquet_file_metadata` and `DESCRIBE` (`connectors/parquet_files.py`), so "no row scanned" rests on that and the time, not on a counter | PASS |
| AC-07: no statistic's minimum or maximum in a response or `workspace.db` | `test_no_statistic_of_a_parquet_file_is_sent_or_saved`: planted text and decimal minimums and maximums, checked present in the file, absent from both responses and the database dump. Failed when the reader was made to put a column's `stats_max` in `created_by` | PASS |
| AC-07: shown as "40,000,000 rows", "326 row groups", "Written by parquet-cpp-arrow version 17.0.0" | `ParquetDetails.test.tsx` "a Parquet file shows what its metadata says", with those values; failed while Parquet fell through to the CSV view. e2e `test_connect_parquet.py::test_connecting_a_parquet_file_shows_its_metadata` on the installed wheel: "10,240 rows", "5 row groups", "Written by DuckDB version v…", axe clean | PASS |
| AC-08: encrypted, folder, `s3://` and `https://`, cut short, named `.parquet` but not one; each message, nothing saved | `test_an_encrypted_parquet_file_is_refused`, `test_a_folder_is_refused`, `test_a_remote_path_is_refused` (both), `test_a_parquet_file_cut_short_is_refused` (and no path in the message), `test_a_file_named_parquet_that_isnt_one_is_refused`: exact message, 422, empty source list. All 6 seen failing first, the cut-short file with a 500. e2e `test_a_remote_path_is_refused_with_its_message`, axe clean | PASS |
| AC-09: each type's native and normalised type | `test_each_parquet_type_shows_its_native_and_normalised_type`, the 9 types as the criterion lists them; failed on `TIMESTAMP_NS` as `string` | PASS |
| AC-09: list, struct and map say so | `ParquetDetails.test.tsx` "each column shows its native type, its type, and whether it's a list, struct or map"; e2e `pair string (list) INTEGER[]` | PASS |
| AC-29 (this ticket's part): CSV text and Parquet told apart by content, and the source says which | `test_a_parquet_file_named_csv_is_read_as_parquet`, `test_csv_text_named_parquet_is_read_as_a_csv`: `kind` is `parquet` and `csv`. Both failed with detection by name. `test_csv_text_that_starts_like_parquet_is_read_as_a_csv` (`PARENT_ID`, `PAR1_CODE` headers), after review finding 1 | PASS |
| Expected evidence: the migration adding `xlsx` and `parquet`, up and down | `test_a_workspace_goes_back_to_0009_with_its_csv_and_database_sources` (rows kept, columns gone, up again), `test_a_workspace_with_a_parquet_source_stays_at_0010` (refused, unchanged); both failed with no `downgrade` | PASS |

Also built, outside the criteria: until DRIVN-62 and DRIVN-63, a Parquet source's rows, settings and profile start
are refused with 409, "This Parquet file can't be previewed yet.", "A Parquet file has no file settings." and "This
Parquet file can't be profiled yet." (`test_a_parquet_source_isnt_read_as_a_csv`, 3 cases, seen crashing first).
The three messages were agreed with the user in chat on 2026-11-25.

Checks run, on the tree above (after the review's fixes):
- `uv run --frozen pytest tests/integration tests/golden`: 2649 passed, 8 skipped (platform and by-design skips; the
  PostgreSQL fixture was up), exit 0, in 8 min 55 s.
- `uv run --frozen pytest tests/e2e`: 98 passed, exit 0, in 3 min 50 s.
- `pnpm --dir apps/web exec tsc --noEmit`: exit 0. `pnpm --dir apps/web test`: 265 passed, 3 runs of 3 after the last
  change. `Preview.test.tsx` "the preview opens on the first 50 rows" times out in some full runs, on `main` too (2 of
  3 there), so it isn't this branch's.
- detect-secrets 1.5.0 over the 15 changed and new files: exit 0. No dependency changed, so `pip-audit` and
  `pnpm audit` weren't run again.
- `apps/web` hasn't changed since `tsc` and Vitest above; the review's fixes are all in Python.
- `ai-skills knowledge check`: only `index/graph.yaml` out of date. The research note now names `literal_path`.

Not verified:
- A file written by `parquet-cpp-arrow version 17.0.0`: the venv has no pyarrow, so every Parquet file here is
  written by DuckDB, and the view's strings are checked with the API's values in Vitest. The golden fixture (DRIVN-72)
  is independent of DuckDB's writer.
- A file whose footer is in plain text but whose columns are encrypted (magic `PAR1`); DuckDB writes only the
  encrypted-footer kind (`PARE`), which is what's refused.
- A Parquet path with `[`, `*` or `?` is read literally: probed by hand (`we[ir]d*.parquet` beside
  `weird-decoy.parquet` read only itself) and by the reviewer (`a[1].parquet`), no test.
- `C://data` on Windows: the test runs on every platform, but only Windows reads it as a drive path.
- CI on GitHub, the e2e on macOS and Windows.

## Review (2026-11-25, by the independent reviewer, on the uncommitted tree)
Verdict: not ready, for finding 1; the migration found sound (a fresh schema differs from 0009 only in `source`, the
same three autoindexes, 21 CHECK cases right per kind, the downgrade's table identical to 0009's). Each finding was
reproduced before acting on it, and each fix driven by a test seen failing.
1. CSV text starting `PARE` or `PAR1` was read as Parquet (blocker): `PARENT_ID,NAME` was refused as encrypted, a
   regression for CSV. Fixed: a Parquet file starts and ends with the same magic and is at least 12 bytes; a file cut
   short falls to the CSV path, where binary named `.parquet` still gets "This file isn't a Parquet file".
2. A file gone after the path checks raised `FileNotFoundError`, a 500 whose text and log line held the path (risk).
   Fixed: detection refuses with "DrivnX couldn't read this file."
3. A list of a type with parameters, `DECIMAL(10,2)[]`, was `number` (risk). Fixed: any list is `string`.
4. `UTINYINT`, `USMALLINT` and `UINTEGER` were `string`, as DRIVN-60's Conflicts section says they mustn't be (risk).
   Fixed: `integer`.
5. "This path is not a regular file, so it can't be read as a CSV." (nit). Changed, as the user chose, to "This path
   is not a regular file, so DrivnX can't read it."; the field label and the empty panel's "CSV" stay for DRIVN-70.
6. The remote check took `C://data`, a Windows path, for a URL (nit). Fixed, as the user chose: a scheme has two
   letters or more; `file://` stays refused with the same message.
7. `TIME WITH TIME ZONE` was `string` (nit). Fixed, as the user chose: `time`.
