---
id: DRIVN-69
title: "CSV: correct a column's type"
feature: DRIVN-60
feature_revision: 1
stories: [US-01, US-04]
criteria: [AC-16, AC-17, AC-27]
blocked_by: []
state: ready
external_key: DRIVN-69
status: CONFIRMED
---

# CSV: correct a column's type

Plane: [DRIVN-69](https://app.plane.so/drivnx/browse/DRIVN-69/)

## Outcome
A user corrects a CSV column's type to integer, number, boolean, date, time, timestamp or text, with a date format from a list that starts with the detected one, or puts the detected type back. The next profile measures the column with that type, earlier runs are kept and labelled as using earlier settings, and saving is refused while a profile runs.

## Acceptance checklist
- [ ] AC-16 (US-04): Given `orders.csv` whose column `order_date` holds "22/11/2026" and was detected as text, when the user sets its type to date with the format DD/MM/YYYY and saves, then the column shows "date", and the next profile measures it as a date and counts "2026-13-01" as 1 value that doesn't fit. The format list shows the detected format first. Choosing "As detected" puts the detected type back.
- [ ] AC-17 (US-04): Given a run of `orders.csv` made before that type correction, then it's kept, labelled "Used earlier settings", and its findings say they aren't current. The column names aren't replaced, as a type correction doesn't change them.
- [ ] AC-27 (US-01, this ticket's part: a CSV column's type; a sheet's range or header is DRIVN-67's): Given a profile run in progress, when the user saves a sheet's range or header, or a CSV column's type, then it's refused with "A profile is running. Wait for it to finish, then save these settings again." and the previous settings stay.

## Expected evidence
Integration tests: the type and format saved as read settings, replacing the dataset (ADR 0002); the next profile counting values that don't fit; "As detected"; an earlier run kept with its label and findings not current; names not replaced; saving refused during a run. Vitest for the type control and format list. e2e correcting a column's type.

## Notes
Meets DRIVN-9's deferred "column types" decision.

## Blocked by
None, it can start now.
