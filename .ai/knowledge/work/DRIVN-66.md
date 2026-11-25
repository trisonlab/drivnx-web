---
id: DRIVN-66
title: "XLSX: connect, list and choose sheets"
feature: DRIVN-60
feature_revision: 1
stories: [US-01]
criteria: [AC-01, AC-02, AC-06, AC-26, AC-28, AC-29]
blocked_by: [DRIVN-61, DRIVN-65]
state: blocked
external_key: DRIVN-66
status: CONFIRMED
---

# XLSX: connect, list and choose sheets

Plane: [DRIVN-66](https://app.plane.so/drivnx/browse/DRIVN-66/)

## Outcome
A user connects a workbook and sees its sheets in workbook order, hidden ones marked and chart sheets left out, and chooses which to profile; the choice stays after a restart. Connecting the same path again opens the existing source. An `.xls` file, a password-protected workbook, a file that isn't one, one over the unpack limit and one with no sheets of cells are refused with their messages, and nothing is saved.

## Acceptance checklist
- [ ] AC-01 (US-01): Given `sales.xlsx`, when the user connects it, then the Data Explorer shows the source `sales.xlsx` with the sheets `Orders` and `Customers (hidden)` in workbook order, `Trend` isn't listed, and only `Orders` is chosen.
- [ ] AC-02 (US-01, this ticket's part: choosing sheets and keeping them after a restart; refusing a preview is DRIVN-67's and a profile DRIVN-68's): Given `sales.xlsx` with `Orders` and `Customers` chosen and saved, when DrivnX restarts, then both are still chosen. When the user previews or profiles a sheet that isn't chosen, then it's refused with "Choose this sheet to preview or profile it."
- [ ] AC-06 (US-01): Given one of these files, when the user connects it, then it's refused with its message and nothing is saved: an `.xls` file, "This is an older Excel file (.xls), which DrivnX can't read. Save it as .xlsx in Excel, then connect it again."; a password-protected workbook, "This workbook is password-protected, so DrivnX can't read it. Save a copy without a password, then connect that."; a file named `.xlsx` that isn't a workbook, "This file isn't an Excel workbook, so it can't be read as one."; a workbook over the decompression limit, "This workbook would take more than 2 GB to unpack, so DrivnX won't read it."
- [ ] AC-26 (US-01): Given `sales.xlsx` already connected with a corrected range, when the user connects the same path again, then the existing source opens with its sheets, selection and settings.
- [ ] AC-28 (US-01): Given a workbook with no worksheet, only chart sheets, when the user connects it, then it's refused with "This workbook has no sheets with cells to read." and nothing is saved.
- [ ] AC-29 (US-01, this ticket's part: a workbook told apart by content; CSV text and Parquet are DRIVN-61's): Given a file named `orders.csv` whose content is an XLSX workbook, or `sales.xlsx` whose content is CSV text, then DrivnX reads it by what its content is, not its name, and the source says which it was read as.

## Expected evidence
Integration tests: the sheet list from `xl/workbook.xml` with hidden and chart sheets; the selection saved and kept after a restart with real processes; the same path opening the existing source; each refusal with nothing saved; a workbook named `.csv` read as a workbook. Vitest for listing and choosing sheets. e2e connecting `sales.xlsx` against the installed wheel, with axe.

## Notes
Each chosen sheet is a dataset, as a database's objects are (DRIVN-45).

## Blocked by
DRIVN-61, DRIVN-65.
