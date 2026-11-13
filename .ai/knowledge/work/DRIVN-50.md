---
id: DRIVN-50
legacy_id: CFX-APP-postgres-T05
title: "Preview a chosen object"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-02]
criteria: [AC-12]
blocked_by: [DRIVN-49]
state: blocked
external_key: DRIVN-50
status: CONFIRMED
---

# Preview a chosen object

Plane: [DRIVN-50](https://app.plane.so/drivnx/browse/DRIVN-50/) · `CFX-APP-postgres-T05`

## Outcome
A user pages through a chosen object's rows, 50 at a time, without DrivnX saving them.

## Acceptance checklist
- [ ] AC-12 (US-02): Given a chosen object, when its preview opens, then it shows 50 rows at a time with next and previous, the rows aren't saved, the response is `Cache-Control: no-store`, and cells follow AC-28 and AC-29 of the first slice.

## Expected evidence
API and component tests for paging, `Cache-Control: no-store`, and cells cut and shown as text as in the first slice.

## Blocked by
DRIVN-49
