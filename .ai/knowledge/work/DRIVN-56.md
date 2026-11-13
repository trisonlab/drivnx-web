---
id: DRIVN-56
legacy_id: CFX-APP-postgres-T11
title: "Values on request"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-05]
criteria: [AC-28]
blocked_by: [DRIVN-53, DRIVN-55]
state: blocked
external_key: DRIVN-56
status: CONFIRMED
---

# Values on request

Plane: [DRIVN-56](https://app.plane.so/drivnx/browse/DRIVN-56/) · `CFX-APP-postgres-T11`

## Outcome
A user asks for a column's values and gets them from the database with the run's method and seed, labelled as the same rows only if the object hasn't changed, and a clear message when the database can't be reached.

## Acceptance checklist
- [ ] AC-28 (US-05): Given a column in a completed run, when the user asks for its values, then they're computed by querying the database with that run's method and seed, labelled with that scope, the time computed, and that they're the same rows only if the object hasn't changed since the run (for a view, that the rows may differ), cut at 1,000 characters, and sent with `Cache-Control: no-store`. Given the database unreachable, then a clear message shows, and the saved metrics still show.

## Expected evidence
API tests for each scope label, a changed table, a view, `no-store`, values cut at 1,000 characters, and the server stopped.

## Blocked by
DRIVN-53, DRIVN-55
