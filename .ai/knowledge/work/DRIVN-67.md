---
id: DRIVN-67
title: "XLSX: a sheet's range, header, types and preview"
feature: DRIVN-60
feature_revision: 1
stories: [US-01, US-03]
criteria: [AC-02, AC-03, AC-04, AC-05, AC-10, AC-27]
blocked_by: [DRIVN-66]
state: blocked
external_key: DRIVN-67
status: CONFIRMED
---

# XLSX: a sheet's range, header, types and preview

Plane: [DRIVN-67](https://app.plane.so/drivnx/browse/DRIVN-67/)

## Outcome
A user corrects a sheet's range and whether its first row is the header, and sees each column typed from more than its first row: a mixed column falls back to text and says so, dates and booleans are typed, and 1904-date workbooks read the same dates as 1900-date ones. They preview a chosen sheet a page at a time, and saving is refused while a profile runs.

## Acceptance checklist
- [ ] AC-03 (US-01): Given a sheet with the title "Q3 sales" in A1 and its table in B3:F200, when the user corrects the range to `B3:F200` with the first row as the header and saves, then the columns are the names in B3:F3 and the preview starts at row 4. Given a range that isn't one, such as `F200:B3`, `B3` or `B3:F`, then saving is refused with "Enter a range like B3:F200, from its top-left cell to its bottom-right cell." and the previous settings stay. Given no header, then the columns are named `column0`, `column1` and so on, as a CSV's are.
- [ ] AC-04 (US-01): Given a column `Amount` with the cells 10, 12.5, `n/a` and 7, then it's read as text, its column says "Mixed values, read as text", and a profile counts 1 of its 4 cells as not fitting the type `number`. Given a column of cells formatted as dates, then it's a date column, and a column of TRUE and FALSE is a boolean one. A column whose first cell is empty is typed from the cells that aren't.
- [ ] AC-05 (US-01): Given a cell that Excel shows as 22/11/2026 in a 1904-date workbook and one in a 1900-date workbook, then DrivnX shows and profiles both as 2026-11-22.
- [ ] AC-10 (US-03, this ticket's part: a sheet; a Parquet file's is DRIVN-62's): Given a chosen sheet or a Parquet file, when the user opens its preview, then 50 rows a page are shown (up to 200 when asked), each cell cut at 1,000 characters, every response sent with `Cache-Control: no-store`, and no row of `workspace.db` holds a value from the preview.
- [ ] AC-27 (US-01, this ticket's part: a sheet's range or header; a CSV column's type is DRIVN-69's): Given a profile run in progress, when the user saves a sheet's range or header, or a CSV column's type, then it's refused with "A profile is running. Wait for it to finish, then save these settings again." and the previous settings stay.
- [ ] AC-02 (US-01, this ticket's part: refusing a preview of a sheet that isn't chosen): Given `sales.xlsx` with `Orders` and `Customers` chosen and saved, when DrivnX restarts, then both are still chosen. When the user previews or profiles a sheet that isn't chosen, then it's refused with "Choose this sheet to preview or profile it."

## Expected evidence
Integration tests: a range and header correction and the columns and first previewed row it gives; each invalid range refused with the settings kept; no header giving `column0`…; the mixed column's type and message; date and boolean columns; a first empty cell; 1900 and 1904 dates; preview limits, `no-store` and a raw scan; a sheet not chosen refused; saving refused during a run. Vitest for the settings and preview. e2e correcting a range.

## Notes
The ADR on how a sheet is typed is written here; AC-25 checks it in DRIVN-72. A header correction replaces saved column names as a CSV's does (DRIVN-9 AC-22).

## Blocked by
DRIVN-66.
