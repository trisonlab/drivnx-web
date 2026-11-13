---
id: DRIVN-57
legacy_id: CFX-APP-postgres-T12
title: "Earlier runs and restarts"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-06]
criteria: [AC-31, AC-32, AC-33]
blocked_by: [DRIVN-53]
state: blocked
external_key: DRIVN-57
status: CONFIRMED
---

# Earlier runs and restarts

Plane: [DRIVN-57](https://app.plane.so/drivnx/browse/DRIVN-57/) · `CFX-APP-postgres-T12`

## Outcome
A user goes back to earlier runs of a source, knows results describe each object at the run's time and when they're out of date, and finds a run that was going when DrivnX stopped marked interrupted, with nothing left running on the server.

## Acceptance checklist
- [ ] AC-31 (US-06): Given several runs of a source, then the user can list them with mode, status, time and objects, open any completed one, and the newest completed run is shown by default.
- [ ] AC-32 (US-06): Given results, then they say they describe the object at the run's time. Given an object whose columns or types differ from the run's, then the Profile tab says the results are out of date.
- [ ] AC-33 (US-06): Given a restart, then completed runs, their metrics and findings are as they were, a run that was in progress shows as `interrupted`, and no query DrivnX started is still running on the server.

## Expected evidence
Integration tests for the runs list, out-of-date results after a column change, and a restart during a run checked in `pg_stat_activity`.

## Blocked by
DRIVN-53
