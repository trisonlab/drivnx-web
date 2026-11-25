---
id: DRIVN-68
title: "XLSX: profile chosen sheets, end to end"
feature: DRIVN-60
feature_revision: 1
stories: [US-01, US-03]
criteria: [AC-02, AC-11, AC-13, AC-14, AC-31]
blocked_by: [DRIVN-67, DRIVN-64]
state: blocked
external_key: DRIVN-68
status: CONFIRMED
---

# XLSX: profile chosen sheets, end to end

Plane: [DRIVN-68](https://app.plane.so/drivnx/browse/DRIVN-68/)

## Outcome
A user profiles a workbook's chosen sheets in one run, fast (each sheet's first 100,000 rows, labelled first rows) or full (every row), and cancels it within 5 s. A completed run shows the CSV profile's metrics, findings, values on request and earlier runs for each sheet, and a moved or unreadable workbook fails the run with its message.

## Acceptance checklist
- [ ] AC-11 (US-03): Given `sales.xlsx` with both sheets chosen, when the user starts a fast profile, then one run reads `Orders`' first 100,000 rows and then all 900 of `Customers`, each labelled "first rows, not random". When the user starts a full profile, then the run reads every row and shows "250,000 rows" and "900 rows".
- [ ] AC-13 (US-03, this ticket's part: a sheet; a Parquet file's is DRIVN-63's): Given a full profile of a 1,000,000-row sheet or a 1 GB Parquet file, when the user cancels it 3 s after it starts, then it stops within 5 s, its status is `cancelled`, and none of its results or findings appear as current.
- [ ] AC-14 (US-03, this ticket's part: a sheet; a Parquet file's is DRIVN-64's): Given a completed run of `Orders` or `events.parquet`, then each column shows the CSV profile's metrics with their scope and precision; the CSV profile's findings and sensitive-field rules apply; values on request are computed now from the run's own rows (the sheet's first rows, or the same seeded row groups) and never saved; and earlier runs are listed and opened as a CSV's. When the file's size or modified time has changed since the run, then the Profile tab says "This file has changed since this profile ran, so these results may be out of date."
- [ ] AC-31 (US-03, this ticket's part: a sheet; a Parquet file's is DRIVN-63's): Given a sheet or a Parquet file that moved, was deleted or can't be read, when a run starts or fails partway, then the run ends `failed` with "DrivnX can't find this file. It may have moved or been deleted." or "DrivnX couldn't read this file.", and its results don't appear as current.
- [ ] AC-02 (US-01, this ticket's part: refusing a profile of a sheet that isn't chosen): Given `sales.xlsx` with `Orders` and `Customers` chosen and saved, when DrivnX restarts, then both are still chosen. When the user previews or profiles a sheet that isn't chosen, then it's refused with "Choose this sheet to preview or profile it."

## Expected evidence
Integration tests: one run over two sheets, fast and full, with their row counts and labels; a cancel 3 s into a full run of a 1,000,000-row sheet; metrics, findings and sensitive fields; values on request from the sheet's first rows, never saved; earlier runs; the changed-file message; a deleted workbook failing the run; a sheet not chosen refused. Vitest and an e2e of a completed run. The performance targets for a 1,000,000-row, 5-column sheet measured and recorded (fast at most 10 s, full at most 120 s).

## Notes
Blocked by DRIVN-64 too, as a workbook's run reuses the Parquet ticket's file-run changes (the changed-file message, values on request for a file read another way).

## Blocked by
DRIVN-67, DRIVN-64.
