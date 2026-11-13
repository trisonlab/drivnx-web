---
id: DRIVN-53
legacy_id: CFX-APP-postgres-T08
title: "Full profile, time limit and cancel"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-04]
criteria: [AC-19, AC-20, AC-21, AC-22]
blocked_by: [DRIVN-51]
state: blocked
external_key: DRIVN-53
status: CONFIRMED
---

# Full profile, time limit and cancel

Plane: [DRIVN-53](https://app.plane.so/drivnx/browse/DRIVN-53/) · `CFX-APP-postgres-T08`

## Outcome
A user runs a full profile for exact results and row counts, with a time limit per object that stops one object and goes on to the next, and a cancel that leaves nothing running on the server. A lost connection fails the run with a clear reason.

## Acceptance checklist
- [ ] AC-19 (US-04): Given chosen objects, when the user chooses Full profile, then every row of each object is read, with exact results and each object's exact row count.
- [ ] AC-20 (US-04): Given a full profile and a per-table time limit, when an object's profile reaches the limit, then its scan stops within 5 s, no query from DrivnX is left running on the server, the object shows "stopped at the time limit, no results", the run goes on to the next object, and the finished run says which objects have no results.
- [ ] AC-21 (US-04): Given a run in progress, when the user cancels it, then it stops within the target under Non-functional, no query from DrivnX is left running on the server, its status is `cancelled`, and none of its results or findings appear as current.
- [ ] AC-22 (US-04): Given a connection lost, a server restart or a privilege revoked during a run, then the run ends `failed`, the message names the object and the reason, and its results don't appear as current.

## Expected evidence
Integration tests for exact results, the limit reached on one object, a cancel checked in `pg_stat_activity`, and a server restart and a revoked privilege mid-run. Times for a full profile and a cancel recorded on the fixture.

## Blocked by
DRIVN-51
