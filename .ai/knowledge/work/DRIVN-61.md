---
id: DRIVN-61
title: "Parquet: connect and read its metadata"
feature: DRIVN-60
feature_revision: 1
stories: [US-01, US-02]
criteria: [AC-07, AC-08, AC-09, AC-29]
blocked_by: []
state: ready
external_key: DRIVN-61
status: CONFIRMED
---

# Parquet: connect and read its metadata

Plane: [DRIVN-61](https://app.plane.so/drivnx/browse/DRIVN-61/)

## Outcome
A user connects a Parquet file by its path and, within a second, sees its columns with their native and normalised types, its row count, its row groups and the program that wrote it, all read from its metadata without scanning a row. An encrypted file, a folder, a remote path and a file that isn't Parquet are refused with their messages, and nothing is saved. A file is read by what its content is, not its name.

## Acceptance checklist
- [ ] AC-07 (US-02): Given `events.parquet`, when the user connects it, then within 1 s its columns with their native types, "40,000,000 rows", "326 row groups" and "Written by parquet-cpp-arrow version 17.0.0" are shown, read from its metadata without scanning a row. No response and no row of `workspace.db` holds a minimum or maximum from its statistics.
- [ ] AC-08 (US-02): Given one of these paths, when the user connects it, then it's refused with its message and nothing is saved: an encrypted Parquet file, "This Parquet file is encrypted, so DrivnX can't read it."; a folder, "This path is a folder. Enter the path to a CSV, XLSX or Parquet file; a folder of Parquet files isn't supported yet."; `s3://bucket/events.parquet` or `https://example.com/events.parquet`, "DrivnX reads files on this computer only. Enter the full path to a file."; a file named `.parquet` that isn't one, or one cut short, "This file isn't a Parquet file, so it can't be read as one."
- [ ] AC-09 (US-02): Given Parquet columns of the types `UBIGINT`, `DECIMAL(38,2)`, `TIMESTAMP_NS`, `TIMESTAMP WITH TIME ZONE`, `UUID`, `BLOB`, `INTEGER[]`, `STRUCT(a INTEGER)` and `MAP(VARCHAR, INTEGER)`, then each column shows its native type and its type in ADR 0003's vocabulary: integer, number, timestamp, timestamp, string, string, and string for the list, struct and map, which also say "list", "struct" and "map".
- [ ] AC-29 (US-01, this ticket's part: CSV text and Parquet told apart by content; a workbook's is DRIVN-66's): Given a file named `orders.csv` whose content is an XLSX workbook, or `sales.xlsx` whose content is CSV text, then DrivnX reads it by what its content is, not its name, and the source says which it was read as.

## Expected evidence
Integration tests: metadata read with no row scanned and no statistics minimum or maximum in a response or in `workspace.db`; each type in AC-09 with its native and normalised type; each refusal and its message with nothing saved; a Parquet file named `.csv`, and CSV text named `.parquet`, read by content. The migration adding the `xlsx` and `parquet` source kinds, up and down. Vitest for the metadata view. e2e connecting a Parquet file against the installed wheel, with axe. Connecting a 40,000,000-row file timed against 1 s.

## Notes
The migration adds both new source kinds, `xlsx` and `parquet`, so DRIVN-66 needs none for that. `normalised_type` needs Parquet's types (AC-09), which it maps to `string` today.

## Blocked by
None, it can start now.
