---
id: DRIVN-53
legacy_id: CFX-APP-postgres-T08
title: "Full profile, time limit and cancel"
feature: CFX-APP-postgres
feature_revision: 3
stories: [US-04]
criteria: [AC-19, AC-20, AC-21, AC-22, AC-23]
blocked_by: [DRIVN-51]
state: ready
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
- [ ] AC-23 (US-04), its full-profile half: Given a full profile, then it creates nothing in the current directory, any spill files are inside `<workspace>/duckdb-tmp/`, and DrivnX reads the server over at most one connection for it. Stopping a read may open one more connection for under a second, to cancel the read on the server (revision 3). DRIVN-51 proved the rest for a fast profile.

## Expected evidence
Integration tests for exact results, the limit reached on one object, a cancel checked in `pg_stat_activity`, and a server restart and a revoked privilege mid-run. Times for a full profile and a cancel recorded on the fixture.

## Blocked by
DRIVN-51

## Plan agreed with the user
Decisions approved in chat, 2026-11-17, after probes on the fixture (ADR 0009, Measured): DuckDB's interrupt stops a
table's scan in 0.05 s, but not a view's read through the scanner nor any `postgres_query` read, which return only once
the server has sent every row.

1. **Stopping a read:** interrupt the job's connection; if the read hasn't returned within 1 s, a second connection
   sends `pg_cancel_backend` for the job's session pid and closes. For a cancel and a time limit, fast and full.
   AC-23 says so from spec revision 3.
2. **Masked patterns on a full profile** come from the object's fast reading, scope `sample`, as the spec assumes.
3. **Duplicate rows** are counted and saved on a full profile, as a CSV's are. Their finding comes with DRIVN-55.
4. **A server restart** is tested by `pg_terminate_backend` on the job's session mid-read, which is what a fast
   shutdown does to it, on all three CI systems.

Approach (ADR 0009): a full profile reads each object whole through DuckDB's scanner with `pg_numeric_as_varchar` and
`pg_array_as_varchar` on, each column cast to `VARCHAR` and `bytea` as `'\x' || lower(hex(...))`, into the job's local
table, and measures it there with `every_row=True`, `count_duplicates=True`, `blank_is_missing=False`. The job attaches
with `pg_pool_max_connections = 1` and `pg_pool_acquire_mode = 'wait'`, loads with `threads = 1`, and on a full run
with `statement_timeout` at the time limit and a margin. An object stopped at its limit is the observation
`time_limit_reached`, value its limit in minutes. No migration.

Steps:
1. Connector: `load_whole_object(connection, object_name, columns, physical_types, table)`; the settings in `attach`.
   Test: each fixture type read whole equals its `::text`.
2. `POST /api/profile-runs` with `mode: "full"` for a database: `time_limit_minutes` 1–120, default 10, in the run's
   scope (422 outside it, or for a file); per object the fast reading's patterns, then the whole read measured; exact
   `rows_read`, scope `full`; `duplicate_rows`; the detail's method `full` (AC-19).
3. AC-23's full half: the one-session and spill tests run for `fast` and `full`.
4. `_Job.server_pid` from `session_pid(connection)`; `_interrupt_until_ended` calls
   `cancel_read(details, password, pid, temp_directory)` once after `STOP_GRACE_SECONDS = 1`. Tests: cancel during a
   full table read, a full view read and a fast view read, each `cancelled` within 5 s, nothing active in
   `pg_stat_activity`, no results current (AC-21).
5. Per-object deadline of `time_limit_minutes × SECONDS_PER_MINUTE` (patched in tests); a stopped object gets
   `time_limit_reached`, the run goes on and ends `completed`; `ObjectReadResponse.stopped_at_time_limit` (AC-20).
6. `_not_read` tells a timeout, a lost connection and a revoked privilege apart, for fast and full reads. Tests:
   `pg_terminate_backend` mid-read, and `REVOKE SELECT` on the next object while one is read, each `failed` naming the
   object and the reason (AC-22).
7. UI: Full profile for a database with a minutes field, "every row, N rows", patterns "from a sample of about N
   rows", "stopped at the time limit, no results" and the run's list of objects without results, Cancel for a
   database run. Vitest, and an e2e full profile and cancel with an accessibility check.
8. Times for a full profile and a cancel on 10 million rows, from `tests/measure_postgres_sample.py`, recorded here;
   the threat register's bounded queries and cancellation; the stale "DRIVN-54" comments that mean this ticket.

Not in this ticket: findings (DRIVN-55), values on request after a full run (DRIVN-56), runs interrupted by a restart
(DRIVN-57), the write warning (DRIVN-52), the golden metrics (DRIVN-54).
