---
id: DRIVN-54
legacy_id: CFX-APP-postgres-T09
title: "Every metric against the golden fixture"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-05]
criteria: [AC-25]
blocked_by: [DRIVN-51]
state: blocked
external_key: DRIVN-54
status: CONFIRMED
---

# Every metric against the golden fixture

Plane: [DRIVN-54](https://app.plane.so/drivnx/browse/DRIVN-54/) · `CFX-APP-postgres-T09`

## Outcome
Every saved metric is right for PostgreSQL's types: dates and times with time zones, Unicode, numeric, boolean, JSON, UUID, arrays and `bytea`, on tables, a partitioned table, a view and a materialized view.

## Acceptance checklist
- [ ] AC-25 (US-05): Given the synthetic fixture database, covering missing values, duplicates, dates and times with time zones, Unicode, numeric and decimal, boolean, JSON, UUID, arrays, `bytea`, high cardinality, a partitioned table, a view and a materialized view, when each object is profiled, then every saved metric equals its known expected value.

## Expected evidence
Golden tests on the fixture database, each expected value worked out independently of the code.

## Blocked by
DRIVN-51
