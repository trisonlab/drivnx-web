---
id: DRIVN-57
legacy_id: CFX-APP-postgres-T12
title: "Earlier runs and restarts"
feature: CFX-APP-postgres
feature_revision: 3
stories: [US-06]
criteria: [AC-31, AC-32, AC-33]
blocked_by: [DRIVN-53]
state: done
external_key: DRIVN-57
status: CONFIRMED
---

# Earlier runs and restarts

Plane: [DRIVN-57](https://app.plane.so/drivnx/browse/DRIVN-57/) · `CFX-APP-postgres-T12`

## Outcome
A user goes back to earlier runs of a source, knows results describe each object at the run's time and when they're out of date, and finds a run that was going when DrivnX stopped marked interrupted, with nothing left running on the server.

## Acceptance checklist
- [x] AC-31 (US-06): Given several runs of a source, then the user can list them with mode, status, time and objects, open any completed one, and the newest completed run is shown by default.
- [x] AC-32 (US-06): Given results, then they say they describe the object at the run's time. Given an object whose columns or types differ from the run's, then the Profile tab says the results are out of date.
- [x] AC-33 (US-06): Given a restart, then completed runs, their metrics and findings are as they were, a run that was in progress shows as `interrupted`, and no query DrivnX started is still running on the server.

## Expected evidence
Integration tests for the runs list, out-of-date results after a column change, and a restart during a run checked in `pg_stat_activity`.

## Blocked by
DRIVN-53

## Plan agreed with the user
Decisions approved in chat, 2026-11-22, after a probe on the fixture (PostgreSQL 17, Linux): a reader killed mid-read
leaves a read that streams rows on the server for 0.01 s, and one that computes before its first row (a view over
`pg_sleep(20)`) for 19.7 s, its end; with `client_connection_check_interval = 500` on the session, 0.19 s. Set
through `postgres_execute` it had no effect; only a session's startup setting did. PostgreSQL refuses a non-zero
value on a Windows server.

1. **Out of date (AC-32)** is from the last refresh: an object is out of date once a refresh replaced its dataset
   (kind or columns changed, or gone), the rule AC-13 refreshes by. No live check when a run is opened.
2. **The default run (AC-31)** for a database is its newest completed one, even if some of its objects changed since,
   with a note naming them. A file keeps its current-settings rule (CFX-APP-csv-profile AC-35).
3. **A hard kill (AC-33):** discovery checks whether the server takes `client_connection_check_interval`; if so, the
   job's session starts with it at 1 s. A clean stop is `stop_jobs()`'s cancel, already built.

Steps:
1. `ListedProfileRun.objects` from the run's scope; `ProfileRunResponse.objects: list[str] | None` as
   `schema.object`. Test: three runs listed newest first with mode, status, time and objects; the oldest completed
   one opens (AC-31).
2. `replaced_datasets(engine, dataset_ids)` in `artifacts/sources.py`; `ObjectReadResponse.out_of_date`. Tests: a
   column added, a type changed and an object dropped are out of date after the refresh, not before; an unchanged
   refresh leaves all current (AC-32).
3. UI: `runsOfSource` (all of a database's runs; a file's current-settings ones) for `shownRun`, `profileStatus` and
   the tab; the runs list names a database run's objects and says "Out of date"; the results say they describe each
   object as it was when the run read it, name the out-of-date objects and mark their captions. Vitest, and the
   e2e Profile journey with an accessibility check.
4. A clean stop: the real `drivnx` terminated during a full run and started again; `[interrupted, completed]`, the
   completed run's detail and findings as before, nothing from DrivnX in `pg_stat_activity` within 5 s. On `big`
   everywhere, on `late_sleepy` off Windows (where `terminate()` is a hard kill) (AC-33).
5. `Discovery.checks_closed_connections`, `attach(..., check_connection=...)` adding
   `-c client_connection_check_interval=1000`, passed from the run's discovery to the job. Tests: the fixture's
   discovery says `True` off Windows and `False` on it, and a query after the check works; a SIGKILLed `drivnx`
   mid fast read of `sleepy` leaves nothing on the server within 5 s after a restart, off Windows, seen failing
   without the setting (AC-33).
6. ADR 0009's decision and Measured rows; this ticket's checklist; the knowledge refresh.

Known limits: on a Windows server a hard kill leaves a computing read until its statement timeout (up to the time
limit plus 30 s on a full profile); after a power loss or a lost network no close reaches the server. Preview, values
and discovery sessions don't get the check; they stop at the 30 s statement timeout (INFERRED: AC-33 is about a run).
Not in this ticket: a live check when a run is opened, ending sessions at the next startup, database findings
(DRIVN-55, which changes `database_run_detail` and `DatabaseResults` too: whichever merges second rebases).

## Decisions in building this
- **The runs list's objects are `object_names`,** as `schema.object` in the order the run read them; the detail's own
  `objects` (how each was read) kept its name and shape. A file's run has `null`.
- **`out_of_date` is per object,** on each of the detail's `objects`, from `replaced_datasets()`: a refresh found it
  gone or of another kind or columns. The run's `current_settings` is false once any is.
- **A refresh fetches the runs and their results again** (`DatabaseObjects.tsx`), found by a test: without it the
  Profile tab said nothing until a reload.
- **The findings panel says why a database run's findings aren't current:** its objects have changed or gone since,
  not a file's "used earlier settings". Found when DRIVN-55 merged, as that run is now shown by default.
- **A stop needed no code:** `stop_jobs()` already cancels on shutdown. Through the real command, a stopped process's
  socket closes and the server's check (step 5) then ends the read too, so that test can't show the cancel (found in
  review): `test_stopping_drivnx_cancels_a_database_runs_read_on_the_server` calls `stop_jobs()` in a process that
  goes on, and fails when shutdown doesn't interrupt and cancel the job's read.
- **Whether the server can check is the server's system, not the tests' machine** (found in review): a Windows
  machine may run the fixture's Linux image in Docker, so the tests ask the server's `version()`.
- **The check is 1 s, set in the session's startup options** (ADR 0009 Decision 7); discovery asks the server with
  `set_config(..., true)` after its catalog's transaction. Preview, values and discovery sessions don't set it.

## Verification (2026-11-22, `c8f6f0f`, rebased on `a0349e5`)
| Criterion | Evidence | Result |
|---|---|---|
| AC-31 | `test_postgres_earlier_runs.py`: names its objects, every run newest first and any completed one opens, a file's run names none; `test_postgres_objects_store.py`; Vitest: newest completed shown after a refresh, list names objects; e2e: an earlier run opens | PASS |
| AC-32 | `test_postgres_earlier_runs.py`: column added, type changed, dropped are out of date after the refresh and not before; an unchanged refresh leaves all current; Vitest: "as it was when this run read it", the note for one and two objects, a refresh shows it at once, the findings panel's reason; e2e: the note after a refresh and on the earlier run, with the accessibility check | PASS |
| AC-33 | `test_postgres_earlier_runs.py`: a stop mid-read (table everywhere, computing view off Windows) keeps the completed run's detail and findings, marks the run `interrupted`, nothing in `pg_stat_activity` within 5 s; a SIGKILL mid-read the same off Windows; `test_postgres_discovery.py`: the server's check and the session's setting | PASS (Linux); Windows NOT RUN |

Checks run: `uv run pytest tests/integration tests/golden` 2587 passed, 8 skipped (on `7216cd6`; only web files
changed since); `uv run pytest tests/e2e` 84 passed (`c8f6f0f`); `pnpm exec vitest run` 253 passed;
`pnpm exec tsc --noEmit` clean; CI's detect-secrets clean; pip-audit and `pnpm audit` no known vulnerabilities;
`ai-skills knowledge check` consistent.

Not verified: a Windows server, where discovery should say `False` and the kill test is skipped (CI's Windows job);
macOS (CI); a network between DrivnX and the server.
