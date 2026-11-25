---
id: DRIVN-72
title: "Golden fixtures, full journeys and checks"
feature: DRIVN-60
feature_revision: 1
stories: [US-03, US-07]
criteria: [AC-15, AC-24, AC-25]
blocked_by: [DRIVN-68, DRIVN-69, DRIVN-70, DRIVN-71]
state: blocked
external_key: DRIVN-72
status: CONFIRMED
---

# Golden fixtures, full journeys and checks

Plane: [DRIVN-72](https://app.plane.so/drivnx/browse/DRIVN-72/)

## Outcome
Golden XLSX and Parquet fixtures, made by a script independent of DrivnX's readers, prove every saved metric; full journeys through browsing, connecting, profiling and values on request leave no cell value in `workspace.db`; and the threat register and ADRs record what's built.

## Acceptance checklist
- [ ] AC-15 (US-03): Given the golden fixtures profiled fast and full, with values on request opened for every column, then a raw scan of `workspace.db` and its WAL file finds no cell value from them, only counts, percentages, text lengths, masked patterns, column names and sheet names.
- [ ] AC-24 (US-07): Given an XLSX golden fixture (typed columns, a mixed column, dates, booleans, empty cells, Unicode text, a hidden sheet, and a second workbook with 1904 dates) and a Parquet golden fixture (each type in AC-09, nulls, Unicode text, several row groups), made by a script independent of DrivnX's readers, then every saved metric equals its known value.
- [ ] AC-25 (US-07): Given the threat register and the ADRs, then the "Huge or malformed input" row and R3 record what's built and tested for XLSX and Parquet, and ADRs record the bundled `excel` extension and how a sheet is typed, and how a Parquet file is sampled.

## Expected evidence
Golden tests for each metric of both fixtures; e2e journeys with a raw scan of `workspace.db` and its WAL; the threat register's "Huge or malformed input" and R3 rows and the ADRs read and updated (AC-25 is checked by reading them); every performance figure recorded in one place.

## Notes
Each earlier ticket measures its own targets; this one checks they're recorded and the journeys hold.

## Blocked by
DRIVN-68, DRIVN-69, DRIVN-70, DRIVN-71.
