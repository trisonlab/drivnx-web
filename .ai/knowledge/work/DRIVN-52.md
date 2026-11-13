---
id: DRIVN-52
legacy_id: CFX-APP-postgres-T07
title: "Confirm before profiling with an account that can write"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-03]
criteria: [AC-16]
blocked_by: [DRIVN-51]
state: blocked
external_key: DRIVN-52
status: CONFIRMED
---

# Confirm before profiling with an account that can write

Plane: [DRIVN-52](https://app.plane.so/drivnx/browse/DRIVN-52/) · `CFX-APP-postgres-T07`

## Outcome
Before a profile, a user whose account could change the chosen objects is told which ones and why, and the run starts only once they confirm. A read-only account sees that it can only read.

## Acceptance checklist
- [ ] AC-16 (US-03): Given a role with `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE` on a chosen object, or a superuser, when the user starts a profile, then the screen names the objects or the superuser role and why, and the run starts only after the user confirms. Given a role that can only read the chosen objects, then no warning appears, and the screen says the account can only read them.

## Expected evidence
Fixture roles with and without write privileges and a superuser, the confirmation asked again when the selection or privileges change, and no warning for a read-only role.

## Blocked by
DRIVN-51
