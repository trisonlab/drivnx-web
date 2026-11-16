---
id: DRIVN-51
legacy_id: CFX-APP-postgres-T06
title: "Fast profile of chosen objects, end to end"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-02, US-04, US-05]
criteria: [AC-13, AC-17, AC-18, AC-23, AC-24]
blocked_by: [DRIVN-49]
state: ready
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

## Plan agreed with the user
Approved in chat, 2026-11-16, built after DRIVN-50 (PR #23) merges, with one decision: **in a database, only NULL is
missing.** An empty or whitespace-only string is a value, counted in distinct values and text lengths, unlike a CSV's
blank cell.

Approach: a database run uses the CSV pipeline where it can (one DuckDB connection per job, rows loaded as text columns
`c0…`, `measure_rows`, observations in batches), with each object read on the server through `postgres_query` and the
job's connection at `SET threads = 1`, so the server sees one DrivnX session (AC-23).
- **How each object is read (ADR 0005):** a table, partitioned table or materialized view estimated above 100,000 rows
  with `TABLESAMPLE SYSTEM (p) REPEATABLE (seed)`, p = 100,000 ÷ estimate × 100, labelled "about N rows, sampled by
  page"; one estimated at 100,000 or fewer, or never analysed, whole with `LIMIT 100001`, labelled the whole object, or
  the first rows returned, not random, when the guard is reached; a view, its first 100,000 rows, labelled "first rows
  the view returned, not random".
- **A run starts with a fresh discovery** in the request: it gives the estimates, and a chosen object that is gone or
  whose kind or columns differ from the list's (the rule a refresh uses) refuses the run with a message pointing to the
  refresh (AC-13). That discovery isn't saved.
- **One seed per run** in `profile_run.seed`; each object's method and percentage in the run's `scope`. No migration.
- **Metrics:** missing and distinct counts and percentages for every column; text lengths and masked patterns for text
  columns (`text`, `character varying`, `character`, `citext`, `name`, not arrays); never "values that don't fit the
  type". Distinct values are counted on PostgreSQL's text form, so `1.0` and `1.00` in an unscaled `numeric` are two.
- **Not yet:** a database's full profile (DRIVN-54), findings (DRIVN-55) and values on request (DRIVN-56) are refused
  as not available yet, and the screen says so.

Steps:
1. `measure_rows` gains `count_duplicates` (default `every_row`) and `blank_is_missing` (default `True`).
2. Storage: `read_chosen_objects(engine, source_id)`; `changed_since_listed(engine, source_id, discovery)`, sharing
   `save_discovery`'s comparison; the run listing reads a database run's `scope` (`source_id`, `objects`), with
   `current_settings` true when none of its datasets was replaced.
3. Connector: `reading_for(kind, estimated_rows)` and `load_object_rows(...)`.
4. `POST /api/profile-runs` for a database: full refused, no chosen objects, no password, refused password, changed
   object, run in progress; then the job, which records the object it's on (AC-17).
5. Run API: `current_object` on a run, `objects` with each one's method on its detail, `object` on each metric; a
   database run's values refused as not yet.
6. UI: the Profile tab for a database source, one table per object with its scope label and the run's ID and time
   (AC-24), the running object in the header and the tab, Full profile marked not available yet.
7. AC-23: at most one DrivnX session in `pg_stat_activity` during a run, nothing in the working directory, spill files
   under `<workspace>/duckdb-tmp/`; a Playwright run in the Profile tab with an accessibility check.
