---
id: DRIVN-62
title: "Parquet: preview"
feature: DRIVN-60
feature_revision: 1
stories: [US-03]
criteria: [AC-10]
blocked_by: [DRIVN-61]
state: ready
external_key: DRIVN-62
status: CONFIRMED
---

# Parquet: preview

Plane: [DRIVN-62](https://app.plane.so/drivnx/browse/DRIVN-62/)

## Outcome
A user opens a Parquet file's preview and pages through its rows, 50 a page or up to 200 when asked, with each cell cut at 1,000 characters. Nothing from the preview is saved, and no response is cached.

## Acceptance checklist
- [ ] AC-10 (US-03, this ticket's part: a Parquet file; a sheet's is DRIVN-67's): Given a chosen sheet or a Parquet file, when the user opens its preview, then 50 rows a page are shown (up to 200 when asked), each cell cut at 1,000 characters, every response sent with `Cache-Control: no-store`, and no row of `workspace.db` holds a value from the preview.

## Expected evidence
API tests: page sizes, the 1,000-character cut, `Cache-Control: no-store`, and a raw scan of `workspace.db` and its WAL file finding no previewed value. Vitest for the preview of a Parquet source. e2e opening it.

## Notes
The CSV preview's paging and limits are reused as they are.

## Blocked by
DRIVN-61.
