---
id: DRIVN-50
legacy_id: CFX-APP-postgres-T05
title: "Preview a chosen object"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-02]
criteria: [AC-12]
blocked_by: [DRIVN-49]
state: ready
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

## Plan agreed with the user
Approved in chat, 2026-11-16, with one decision: **the preview's row order** is the primary key's where the object
declares one, so pages never overlap; views and tables without a key come in the server's own order, which
PostgreSQL doesn't promise to keep between pages of a large table read by others at the same time.

Approach: `GET /api/sources/{id}/objects/{object_id}/rows` beside the other object routes, with the CSV preview's page
(50 rows by default, 200 at most, cells cut at 1,000 characters). One query on the server per page, each discovered
column as PostgreSQL's text, `LIMIT` one more than the page and `OFFSET`, so only that page reaches DrivnX and nothing
is written. An object not chosen is refused (409, AC-11); a refused password asks for it again (AC-06); an object
changed since it was listed points to a refresh (409).
