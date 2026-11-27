---
id: DRIVN-64
title: "Parquet: findings, values on request and earlier runs"
feature: DRIVN-60
feature_revision: 1
stories: [US-03]
criteria: [AC-14]
blocked_by: [DRIVN-63]
state: ready
external_key: DRIVN-64
status: CONFIRMED
---

# Parquet: findings, values on request and earlier runs

Plane: [DRIVN-64](https://app.plane.so/drivnx/browse/DRIVN-64/)

## Outcome
A completed Parquet run shows the CSV profile's metrics with their scope and precision, its findings and sensitive-field rules, values on request computed now from the same seeded row groups and never saved, and its earlier runs, and says when the file has changed since it ran.

## Acceptance checklist
- [ ] AC-14 (US-03, this ticket's part: a Parquet file; a sheet's is DRIVN-68's): Given a completed run of `Orders` or `events.parquet`, then each column shows the CSV profile's metrics with their scope and precision; the CSV profile's findings and sensitive-field rules apply; values on request are computed now from the run's own rows (the sheet's first rows, or the same seeded row groups) and never saved; and earlier runs are listed and opened as a CSV's. When the file's size or modified time has changed since the run, then the Profile tab says "This file has changed since this profile ran, so these results may be out of date."

## Expected evidence
Integration tests for each metric's scope and precision on a Parquet column, a finding and a sensitive field, values on request from the run's own row groups with nothing saved, earlier runs listed and opened, and the changed-file message after a size or modified-time change. Vitest and an e2e of a completed run.

## Notes
A Parquet column keeps its native type, so it never has values that don't fit (the spec's Assumptions).

## Blocked by
DRIVN-63.
