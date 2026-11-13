---
id: DRIVN-51
legacy_id: CFX-APP-postgres-T06
title: "Fast profile of chosen objects, end to end"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-02, US-04, US-05]
criteria: [AC-13, AC-17, AC-18, AC-23, AC-24]
blocked_by: [DRIVN-49]
state: blocked
external_key: DRIVN-51
status: CONFIRMED
---

# Fast profile of chosen objects, end to end

Plane: [DRIVN-51](https://app.plane.so/drivnx/browse/DRIVN-51/) · `CFX-APP-postgres-T06`

## Outcome
A user runs a fast profile over the chosen objects and reads each one's metrics, labelled with how its rows were chosen: a page sample with a seed for a table, the whole of a small one, or a view's first rows. A run on an object that changed since discovery is refused.

## Acceptance checklist
- [ ] AC-13 (US-02): Given an object dropped, or whose columns or types changed since discovery, when the user refreshes the source, then the list and the columns show the database as it is now. When a run starts on such an object, it is refused with a message pointing to the refresh.
- [ ] AC-17 (US-04): Given chosen objects and no run in progress, then a second run is refused with a clear message, and the header's profile status and the Profile tab show which object a running run is on.
- [ ] AC-18 (US-04): Given chosen objects, when the user chooses Fast profile, then one run profiles each object: a table, partitioned table or materialized view estimated above 100,000 rows from a page sample of about 100,000 rows with a seed saved in the run, labelled "about N rows, sampled by page"; a smaller or never-analysed one whole, unless it returns more than 100,000 rows, when it's labelled as the first rows returned, not random; and a view from the first 100,000 rows it returns, labelled "first rows the view returned, not random".
- [ ] AC-23 (US-04): Given a profile job, then it creates nothing in the current directory, any spill files are inside `<workspace>/duckdb-tmp/`, and DrivnX holds at most one connection to the server for it, on a full profile too.
- [ ] AC-24 (US-05): Given a completed run, then for each column the Profile tab shows the metrics in the Assumptions, and each metric's scope (about N rows sampled by page, the first rows returned, or the whole object), its precision, and its run's time and ID. An estimated row count is never shown as exact.

## Expected evidence
Integration tests for each kind of object and each label, the seed saved in the run, one connection to the server, spill files in the workspace, and a refused run. A Playwright run in the Profile tab.

## Notes
AC-13 is shared with DRIVN-49.

## Blocked by
DRIVN-49
