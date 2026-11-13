---
id: DRIVN-49
legacy_id: CFX-APP-postgres-T04
title: "Discover and choose objects"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-02]
criteria: [AC-08, AC-09, AC-10, AC-11, AC-13]
blocked_by: [DRIVN-47]
state: blocked
external_key: DRIVN-49
status: CONFIRMED
---

# Discover and choose objects

Plane: [DRIVN-49](https://app.plane.so/drivnx/browse/DRIVN-49/) · `CFX-APP-postgres-T04`

## Outcome
A user sees every schema they can see with its tables, partitioned tables, views and materialized views, opens one to see its columns and declared keys, and chooses what to profile. Objects without read access are shown but can't be chosen, and a refresh shows the database as it is now.

## Acceptance checklist
- [ ] AC-08 (US-02): Given a connected source, then DrivnX lists every schema the user can see, except the system and temporary schemas, with its tables, partitioned tables (partitions hidden), views and materialized views, each with its kind and estimated row count labelled as an estimate. Foreign tables aren't listed.
- [ ] AC-09 (US-02): Given a listed object, when the user opens it, then its columns are shown with their PostgreSQL type, normalised type and whether they allow nulls, and its declared primary key, unique constraints and foreign keys (with the table and columns referenced), without reading any row.
- [ ] AC-10 (US-02): Given an object the user can't `SELECT` from, then it's listed as "no read access" and can't be chosen.
- [ ] AC-11 (US-02): Given the list, when the user chooses whole schemas or single objects and saves, then the selection stays after a restart, and only chosen objects can be previewed or profiled.
- [ ] AC-13 (US-02): Given an object dropped, or whose columns or types changed since discovery, when the user refreshes the source, then the list and the columns show the database as it is now. When a run starts on such an object, it is refused with a message pointing to the refresh.

## Expected evidence
Integration tests against the fixture for each kind of object, foreign tables left out, declared keys, no read access, the selection kept after a restart, and a refresh after a drop and a column change. Discovery timed on 1,000 objects.

## Notes
AC-13 is shared with DRIVN-51: the refresh is here, refusing a run on a changed object is there.

## Blocked by
DRIVN-47
