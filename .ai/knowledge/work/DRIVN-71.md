---
id: DRIVN-71
title: "Recent and Pinned"
feature: DRIVN-60
feature_revision: 1
stories: [US-06]
criteria: [AC-21, AC-22]
blocked_by: [DRIVN-66]
state: blocked
external_key: DRIVN-71
status: CONFIRMED
---

# Recent and Pinned

Plane: [DRIVN-71](https://app.plane.so/drivnx/browse/DRIVN-71/)

## Outcome
The Data Explorer keeps, per workspace, the 10 sources, sheets and database objects opened last, newest first, and any the user pins until unpinned, both after a restart. An item whose file was deleted, or a database object a refresh replaced, stays listed as unavailable and says why when opened.

## Acceptance checklist
- [ ] AC-21 (US-06): Given 12 items opened one after another (sources, sheets and database objects), then Recent lists the last 10, newest first, also after a restart. Opening an item already in Recent moves it to the top, so it's listed once. Opening an item from Recent goes to it.
- [ ] AC-22 (US-06): Given `Orders` pinned, then it stays in Pinned after a restart, until the user unpins it. Given a pinned or recent item whose file has been deleted, or a database object a refresh replaced, then it stays in its list, says "Unavailable", and opening it says why.

## Expected evidence
Store and API tests: Recent's order, limit and moving an item to the top; pinning and unpinning kept after a restart; unavailable items for a deleted file and a replaced object. Vitest for both lists. e2e opening from Recent.

## Notes
Blocked by DRIVN-66 so that sheets are items from the start.

## Blocked by
DRIVN-66.
