---
id: DRIVN-52
legacy_id: CFX-APP-postgres-T07
title: "Confirm before profiling with an account that can write"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-03]
criteria: [AC-16]
blocked_by: [DRIVN-51]
state: done
external_key: DRIVN-52
status: CONFIRMED
---

# Confirm before profiling with an account that can write

Plane: [DRIVN-52](https://app.plane.so/drivnx/browse/DRIVN-52/) · `CFX-APP-postgres-T07`

## Outcome
Before a profile, a user whose account could change the chosen objects is told which ones and why, and the run starts only once they confirm. A read-only account sees that it can only read.

## Acceptance checklist
- [x] AC-16 (US-03): Given a role with `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE` on a chosen object, or a superuser, when the user starts a profile, then the screen names the objects or the superuser role and why, and the run starts only after the user confirms. Given a role that can only read the chosen objects, then no warning appears, and the screen says the account can only read them.

## Expected evidence
Fixture roles with and without write privileges and a superuser, the confirmation asked again when the selection or privileges change, and no warning for a read-only role.

## Blocked by
DRIVN-51

## Verification
Commits `a7750db` and the review's fixes, 2026-11-19, Linux, with the PostgreSQL fixture (PostgreSQL 17).

| Criterion | Evidence | Result |
|---|---|---|
| AC-16, read-only role | `test_postgres_write_warning.py` `test_a_role_that_can_only_read_starts_with_no_warning_and_its_run_says_so`; `test_postgres_discovery.py` `test_a_role_that_can_only_read_can_change_nothing`; `Postgres.test.tsx` "a run whose account could only read says so" (true and false) | PASS |
| AC-16, a role that can write is named with its objects and privileges | `test_a_role_that_can_write_is_refused_naming_the_objects_and_how`; `test_each_object_says_what_the_role_can_change_in_it` (INSERT, UPDATE on one column, DELETE and TRUNCATE); `test_a_grant_on_a_partition_or_a_child_table_counts_for_the_table_it_is_part_of`; `Postgres.test.tsx` "a role that could change chosen objects is warned, naming each and how", "a write warning shows even when a run elsewhere ends as the start is refused" | PASS |
| AC-16, the run starts only after the user confirms | `test_a_role_that_can_write_starts_once_the_user_confirms`; `Postgres.test.tsx` "profiling anyway starts the run with the write access the user was shown" (focus back on Fast profile), "cancelling the write warning starts nothing and returns to Fast profile", "the write warning takes focus on its safe choice, Cancel" | PASS |
| AC-16, asked again only when the selection or privileges change (the Assumption) | `test_the_same_access_starts_again_without_asking`, `test_another_chosen_object_asks_again`, `test_a_grant_or_a_revoke_asks_again` (a grant and a revoke), `test_a_confirmation_of_access_that_has_since_changed_is_refused_with_the_new`; the confirmation kept across a restart and refused for a CSV, `test_postgres_sources.py` | PASS |
| AC-16, a superuser is named by its role | `test_a_superuser_is_refused_naming_its_role`; `test_a_superuser_is_said_to_be_one`; `Postgres.test.tsx` "a superuser is warned that it can change anything, naming the role" | PASS |

Each test was seen failing before its code, or, where an earlier step had built what it relies on, failing with that
code broken (the chosen objects, the privileges or the superuser dropped from the access; any confirmation accepted;
the read-only line shown for every run; the migration's `CHECK` removed).

Checks: `pytest tests/integration tests/golden` 2520 passed, 8 skipped (Windows-only and by-design files) at
`a7750db`; after the review's fixes, every PostgreSQL integration test (278) and `pytest tests/golden` (21) passed,
and `pytest tests/e2e` 80 passed; `pnpm --dir apps/web test` 223 passed, with `Preview.test.tsx` "the preview opens
on the first 50 rows" failing once in some runs, as recorded for DRIVN-51, and passing alone; `pnpm --dir apps/web
build` (with `tsc`) clean; `detect-secrets`, `pip-audit` and `pnpm audit` clean; the pre-commit hook passed;
`ai-skills knowledge check` consistent. Listing 1,000 objects took 0.80 to 0.87 s with the privileges read over each
object's partitions and child tables (0.51 s before this ticket, target 5 s). NOT RUN locally: PostgreSQL 18.7, macOS and
Windows (CI); the browser journey confirming the warning, which is DRIVN-58's (AC-35).

## Plan agreed with the user
Approved in chat, 2026-11-19, with two decisions: **the warning is asked again whenever the chosen objects or the
role's privileges change** since the last confirmation, as the spec's Assumption says, even when the warning would read
the same; and **`INSERT` or `UPDATE` granted on only some columns counts as "can write"**.

Approach: the write access is read in the fresh discovery a run already starts with (AC-13), in the same session and
catalog snapshot, so the job opens no other session (AC-23). A start whose access can write, and isn't the one last
confirmed, is refused with `409 {"detail": {"message": …, "write_access": {...}}}`; the client confirms by sending that
`write_access` back with the start, and the server starts only if it still matches what it reads now. The confirmed
access is kept on the source, and each run records its access in its scope, from which the API says `read_only`.
Rejected: a separate "check access" call (a second session, and the start has to check again anyway); refusing roles
that can write (rejected by the spec); remembering the confirmation per run (lost across a restart).

Steps:
1. **The connector reads write access** (`packages/connectors/postgres.py`): `_OBJECTS` gains
   `has_any_column_privilege(c.oid, 'INSERT')` and `'UPDATE'`, `has_table_privilege(c.oid, 'DELETE')` and `'TRUNCATE'`;
   a `_ROLE` query (`current_user`, `rolsuper`) runs in discovery's transaction. `DiscoveredObject.write_privileges:
   tuple[str, ...] = ()` (in the order INSERT, UPDATE, DELETE, TRUNCATE), `Discovery.role: str`, `Discovery.superuser:
   bool`. Verify in `test_postgres_discovery.py` with roles the test makes and drops: a writer with `INSERT` on one
   table, `UPDATE` on one column of another and `SELECT` alone on a third gets `("INSERT",)`, `("UPDATE",)`, `()`; a
   superuser `superuser=True`; `drivnx_fixture` `()` everywhere.
2. **Keep a confirmation**: migration `0009_write_confirmation.py` (`ALTER TABLE source ADD COLUMN write_confirmed
   TEXT`, JSON, NULL for a CSV or never confirmed); `packages/artifacts/sources.py`
   `read_write_confirmation(engine, source_id) -> dict | None` and `save_write_confirmation(engine, source_id, access)`.
   Verify in `test_postgres_sources.py`: saved, read back, kept across reopening the workspace, NULL for a CSV.
3. **Refuse, then accept, at a run's start**: new `apps/local-api/local_api/write_access.py` with
   `write_access(discovery, chosen) -> dict` (`role`, `superuser`, `objects`: each writable chosen object as `schema.name`
   with its privileges, `chosen`: every chosen object), `can_write(access) -> bool` and `refusal(access) ->
   HTTPException`; `ProfileRunRequest.write_access: dict | None = None`; one block in `start_database_run` after the
   AC-13 check; `start_database_profile(..., write_access=)` keeps it in the run's scope; `ProfileRunResponse.read_only:
   bool | None`. Verify in `test_postgres_profile_api.py`: the read-only role starts and its run says `read_only: true`;
   a writer is refused naming its objects and privileges, with no run; a writer that confirms starts; the same access
   next time starts without asking; another chosen object asks again; a grant or a revoke asks again; a confirmation of
   access that has since changed is refused with the new one; a superuser is refused naming its role.
4. **The Profile tab warns and confirms**: `apps/web/src/api.ts` `WriteAccess`, `startProfile(sourceId, mode,
   writeAccess?)`, `WriteAccessError extends RunRefusedError`; `ProfileTab.tsx` a `WriteAccessConfirmation` alert dialog
   built as `SensitiveConfirmation` is ("Profile anyway", "Cancel"), and the read-only line. Wording: "*role* can change
   N of the chosen objects: public.orders (insert, update), …" or "*role* is a superuser, so it can change anything in
   this database", then "DrivnX opens every session read-only and only reads them"; read-only: "*role* can only read
   the chosen objects." Verify in `Postgres.test.tsx`: the dialog names the objects or the role; Cancel starts nothing
   and focus returns to Fast profile; "Profile anyway" resends the access; a read-only run shows the line; nothing
   relies on colour alone.
5. **Verification and knowledge**: full suites, CI's scans locally, `ai-skills knowledge update`, the ticket `done`.

Tests: AC-16 by the integration tests of step 3 against the real server and the component tests of step 4; the browser
journey confirming the warning is DRIVN-58's (AC-35). DRIVN-51's tests, whose role only reads, keep passing unchanged.

With DRIVN-53: both edit `start_database_run`, `ProfileRunRequest` and `start_database_profile`'s signature; DRIVN-53
changes neither the web app nor the workspace schema, and adds no migration. This ticket's edits there stay a few lines
in one place, and whichever merges second rebases.

Risks: the migration is additive and nullable (two-way door). A database start by a role that can write is now a 409
until confirmed; only the web client calls it. Four privilege checks per column row in discovery, timed against the
1,000-object target.

Out of scope: showing write access in the objects list; roles the account could `SET ROLE` to without inheriting them,
a superuser role among them, as superuser status isn't inherited; an owner that revoked its own privileges, which it
could grant itself again; `CREATE` on a schema; an owner's privileges on a materialized view, reported as PostgreSQL
reports them. A view is warned about as PostgreSQL grants it, even where the view can't be written through (a view
that isn't automatically updatable, `TRUNCATE` on a view): too many warnings, never too few. "Asked again" compares
with the last confirmation, as the Assumption says, so access confirmed, revoked and granted again isn't asked again.

## Review
The independent review found, and this ticket fixed: a grant on a partition or on a table that inherits from a chosen
one wasn't seen, though it changes the chosen table's rows (now read over each object's partitions and child tables);
a write refusal could be cleared with a run-in-progress refusal when a run elsewhere ended as the start was refused;
focus was lost after "Profile anyway"; the read-only line spoke of the current selection and the user name rather than
the run and its account. It confirmed that only the server's own reading is saved, and that DRIVN-53's full profile
gets the warning too once merged (six additive conflicts in `profile_runs_api.py` and `profile_jobs.py`).
