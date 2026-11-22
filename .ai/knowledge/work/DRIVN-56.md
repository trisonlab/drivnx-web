---
id: DRIVN-56
legacy_id: CFX-APP-postgres-T11
title: "Values on request"
feature: DRIVN-45
feature_revision: 3
stories: [US-05]
criteria: [AC-28, AC-27]
blocked_by: [DRIVN-53, DRIVN-55]
state: ready
external_key: DRIVN-56
status: CONFIRMED
---

# Values on request

Plane: [DRIVN-56](https://app.plane.so/drivnx/browse/DRIVN-56/) · `CFX-APP-postgres-T11`

## Outcome
A user asks for a column's values and gets them from the database with the run's method and seed, labelled as the same rows only if the object hasn't changed, and a clear message when the database can't be reached.

## Acceptance checklist
- [ ] AC-28 (US-05): Given a column in a completed run, when the user asks for its values, then they're computed by querying the database with that run's method and seed, labelled with that scope, the time computed, and that they're the same rows only if the object hasn't changed since the run (for a view, that the rows may differ), cut at 1,000 characters, and sent with `Cache-Control: no-store`. Given the database unreachable, then a clear message shows, and the saved metrics still show.
- [ ] AC-27 (US-05), its confirmation half, from DRIVN-55's decision 2: a possibly-sensitive column's values "need the extra confirmation" for a database too. DRIVN-55 saves the flag, which `flagged()` in `profile_runs_api.py` checks by field ID; this ticket proves the values route asks for it before querying the database.

## Expected evidence
API tests for each scope label, a changed table, a view, `no-store`, values cut at 1,000 characters, and the server stopped.

## Blocked by
DRIVN-53, DRIVN-55

## Plan agreed with the user
Approved in chat, 2026-11-22. It builds on DRIVN-57's `out_of_date` and `check_connection` (merged in #30); both
tickets edit `profile_runs_api.py` and `ProfileTab.tsx`.

1. **Full-profile values (A):** every row is re-read, bounded by the run's per-object time limit. The read stops the
   way a profile does (`_stop_at_time_limit`/`_stop_read`: interrupt, then a server cancel after 1 s) and is refused
   with "took longer than this run's time limit of N minutes".
2. **Changed objects (B):** an object that is `out_of_date` since the run is refused with 409 "<object> changed since
   this run. Profile it again to see its values." The run's unchanged objects still answer.
3. **Reading:** fast values are re-read with the run's saved method, `percent` and `seed` through `load_object_rows`;
   full values through `load_whole_object`. Both go to `values_for(..., formats={})` under `_values_lock`. Nothing is
   saved.
4. **Route:** `GET /api/profile-runs/{id}/values?schema=&object=&column=[&confirmed=true]`. `ObjectReadResponse`
   gains `schema` and `name`, so an object isn't found by its ambiguous `schema.name`. `ValuesResponse` gains
   `object`, `method` and `rows_read`.
5. **Refusals:** the run isn't completed; busy; the object was stopped at its time limit; the object is no longer
   chosen (AC-11); out of date; a flagged column without confirmation (AC-27's confirmation half). A connection
   failure goes through `read_failed`, and the UI shows a 422's message.
6. **Label:** the run's reading, plus "the same rows only if <object> hasn't changed since this run", or for a view
   "the view may return other rows than it did for this run".

Steps:
1. `object_values_of`.
2. The route.
3. UI: Show values in `DatabaseResults`.
4. e2e, with a flagged column's confirmation and the accessibility check.
5. Measure on the 10M-row table.
