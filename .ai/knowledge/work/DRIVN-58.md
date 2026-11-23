---
id: DRIVN-58
legacy_id: CFX-APP-postgres-T13
title: "Full journey and checks"
feature: DRIVN-45
feature_revision: 3
stories: [US-03, US-05, US-07]
criteria: [AC-15, AC-29, AC-35, AC-36]
blocked_by: [DRIVN-48, DRIVN-50, DRIVN-52, DRIVN-56, DRIVN-57]
state: ready
external_key: DRIVN-58
status: CONFIRMED
---

# Full journey and checks

Plane: [DRIVN-58](https://app.plane.so/drivnx/browse/DRIVN-58/) · `CFX-APP-postgres-T13`

## Outcome
The whole PostgreSQL journey passes against the installed wheel and the fixture, the fixture's statement log shows DrivnX never wrote, a scan for planted values proves the R3 rule, the new screens pass the accessibility check, and the threat register and ADRs record what was built.

## Acceptance checklist
- [x] AC-15 (US-03): Given the fixture's statement log, when the full journey runs, then DrivnX sent no statement that writes or changes the schema, and every statement carries `application_name` `DrivnX`.
- [x] AC-29 (US-05): Given a full journey with distinctive planted values in the fixture, including the preview and values on request, then a scan of every file in the workspace, during the run and after DrivnX stops, and of its logs at their most detailed finds no value from the database, except masked patterns and the names of schemas, objects, columns and the connection.
- [x] AC-35 (US-07): Given the installed wheel and the fixture, then the journey of adding a connection, confirming the write warning, choosing objects, running a fast profile, running a full profile and opening a finding's evidence passes in Playwright from one origin, with an automated accessibility check on the new screens in each state.
- [x] AC-36 (US-07): Given the threat register and the ADRs, then the "Database or API key leaks" row records what's built and tested, and ADRs record the sampling method from spike 2, the bundled extension and the credential store.

## Expected evidence
Playwright on the installed wheel with axe in each state, the statement log, the planted-value scan during the run and after DrivnX stops, and reading the threat register and the ADRs.

## Blocked by
DRIVN-48, DRIVN-50, DRIVN-52, DRIVN-56, DRIVN-57

## Plan agreed with the user
Approved in chat, 2026-11-22. Tests and documents only, unless the journey finds a bug. One journey helper, walked by
two Playwright tests against the installed wheel and the fixture, as the CSV profile's `walk_the_profile_journey` is
for its DRIVN-9 AC-41 and AC-33. AC-36 is checked by reading, as DRIVN-45's Verification says.

Decisions:
1. **The statement log (AC-15)** is the server's own log, for the journey's role alone: `ALTER ROLE <role> SET
   log_statement = 'all'`, with `ALTER SYSTEM SET log_line_prefix = '%m [%p] app=%a user=%u '` and `pg_reload_conf()`
   for the test's duration, put back in `finally`. No restart, and no other test's sessions are logged. Probed on the
   fixture (PostgreSQL 17.6): each statement is logged with its application and role, and a session that isn't
   DrivnX's shows its own name. Rejected: `pg_stat_statements` (a restart, and no application name), `log_connections`
   per role (the server refuses it after connection start), and changing the fixture's start settings (a restart, and
   `stop` removes the fixture's volume).
2. **The scan (AC-29)** follows the CSV journey's DRIVN-9 AC-33 test: every file in the workspace while DrivnX runs,
   after the journey, then again after it stops, and everything it printed at `--log-level trace`. Not mid-profile:
   DuckDB's spill files in `duckdb-tmp/`, which AC-23 allows, hold rows while a job runs (INFERRED: deleted when the
   job's connection closes).
3. **The journey's role can write** one of its objects, so the write warning is confirmed (AC-35) and AC-15 shows
   DrivnX doesn't write even when it could.

Steps:
1. `server_log_mark()` and `server_log_since(mark)` in `tests/postgres_fixture.py`, the one place the setup depends on
   the platform: the own server's `server.log` from an offset, or `docker compose logs --since`. A `logged_role`
   fixture sets the prefix and the role's `log_statement`, and puts both back. Test in `test_postgres_fixture.py`: a
   role's `SELECT 1` as `DrivnX` shows as `app=DrivnX user=<role> … statement: SELECT 1`, another application's as its
   own.
2. `walk_the_database_journey(page, at_each_state)` in `tests/e2e/test_postgres_journey.py`: schema
   `drivnx_e2e_journey` with planted values in every text, number and date column (a possibly-sensitive `email`, a
   table the writer may `INSERT` into, a view), matching the CSV journey's `PLANTED` while no name does. Add the
   connection as the writer and choose the objects; preview one; Fast profile, the write warning, Profile anyway; Full
   profile; a finding's evidence; a column's values, and the flagged `email` after its confirmation. AC-35's test:
   `violations(page) == []` in each state, every request to the one origin.
3. AC-29 and AC-15 on the same journey, DrivnX at `--log-level trace`, the role logged: `assert_no_value_kept_or_logged`
   from the CSV journey, with each planted value checked to match first and a masked pattern found in the workspace;
   then every statement of the role's in the server's log has `app=DrivnX` and is of a read-only form, an allow-list
   written from a first run's real log, failing on anything else by naming it. Seen failing with a planted value
   logged and an `INSERT` sent as the role, on purpose.
4. AC-36: the threat register's "Database or API key leaks" row says what's built and tested (the credential store,
   DRIVN-48; masking and values never saved; the R3 scan; the password scan, AC-30; the statement log), and ADRs 0005,
   0006 and 0008 are read against AC-36, with where each records it.
5. Verification, the knowledge refresh (with DRIVN-56's), and DRIVN-45's Verification.

Risks: `ALTER SYSTEM` changes the shared fixture until it's put back, in the fixture's `finally`; Compose
logs on Linux only, the own server's log elsewhere; the Windows server's log is first seen in CI. Out of scope: one
journey helper for CSV and PostgreSQL together.

Changed in verification (2026-11-22): the fixture's start doesn't put the log settings back, as the Risks first said.
A run killed mid-test leaves the prefix, which the next test that reads the log sets again and puts back, and a test
role's `log_statement`, which goes when the next setup drops the role; CI starts a fresh fixture for each job. A reset
in `start` would need a superuser connection it doesn't have, for how a local log reads.

## AC-36, read (step 4)
On 2026-11-22, on `8210cc5`, each document read against the code it names:
- **The threat register's "Database or API key leaks" row** (`docs/threat-model/threat-register.md`) now records
  what's built and tested: passwords in the OS credential store or in memory (DRIVN-47, DRIVN-48), AC-30's browser
  scan of the workspace, the logs at trace level, browser storage and URLs, and a database's values never saved
  (AC-12, AC-28) with AC-29's scan from this ticket. Exports and API keys stay planned: neither exists yet. AC-15's
  statement log went in the "DrivnX changes a connected database" row, which it tests, and R3 now covers PostgreSQL.
- **The sampling method from spike 2:** ADR 0005 Decisions 1 to 4 (`TABLESAMPLE SYSTEM … REPEATABLE (seed)` at
  100,000 ÷ the estimate, a small table read whole with a guard of 100,001 rows, a view's first 100,000 rows, values
  with the run's method and seed), with spike 2's numbers under Measured. Matches `SAMPLE_ROWS` and the sample clause
  in `packages/connectors/postgres.py`. Decision 6 is amended by ADR 0009, as its status says.
- **The bundled extension:** ADR 0006 Decisions 1 to 3 (one wheel for `linux_amd64`, `osx_arm64` and
  `windows_amd64`, sha256-pinned in `hatch_postgres.py`, loaded by path with the signature check on). Matches
  `duckdb==2.0.0` in `pyproject.toml`, the hash check in `hatch_postgres.py`, `load_postgres` raising
  `PostgresUnavailable`, and no `allow_unsigned_extensions` anywhere in the code.
- **The credential store:** ADR 0008 Decisions 1 to 9 (keyring behind `KeptPasswords`, only Keychain, Credential
  Manager or Secret Service, one entry per source, `local_machine` on Windows, written only with a password the server
  took). Matches `os_credential_store()` and `entry()` in `local_api/credentials.py`, its call in `cli.py`, and
  `KeptPasswords` in `postgres_api.py`. Amends ADR 0007 Decision 1, as 0007's status says.

## Verification (2026-11-22, on `ba7c8d0`, with only this file changed since)
| Criterion | Evidence | Result |
|---|---|---|
| AC-15: no statement that writes or changes the schema | `test_postgres_journey.py::test_the_database_journey_keeps_no_value_and_sends_only_drivnxs_reads`: the server logs every statement of the journey's role, which may `INSERT` into `orders`; its 329 statements each match a read-only form (a read-only transaction, `COMMIT`, `ROLLBACK`, `DISCARD ALL`, the scanner's checks and catalog, `COPY (SELECT …) TO STDOUT`). Fails naming `INSERT INTO drivnx_e2e_journey.orders VALUES ('mut', 1)` when that's sent through DrivnX's session, which the server refuses as read-only | PASS |
| AC-15: every statement carries `application_name` `DrivnX` | The same test: every entry of the role's has `app=DrivnX`; fails on the first entry with the name changed to `DrivnY`. `test_postgres_fixture.py`: a logged role's statements show their application, `DrivnX` or another, and a role not logged leaves nothing | PASS |
| AC-29: no value from the database in the workspace, during the run and after DrivnX stops, or in its logs at their most detailed | The same test, with `assert_no_value_kept_or_logged` from the CSV journey after the preview and values on request (`orders.code`, then the flagged `email` after its confirmation): every planted value matches `PLANTED` first, the masked email pattern is in the workspace, and DrivnX printed uvicorn's trace lines. Fails with 36 planted values found when the values worked out are logged | PASS |
| AC-35: the journey from one origin, with the accessibility check in each state | `test_postgres_journey.py::test_the_database_journey_on_the_installed_wheel_from_one_origin`: connection as a role that can write, the write warning confirmed, objects chosen, a preview, a fast and a full profile, a finding's evidence, values; `violations(page) == []` in 10 states, every request to the launch origin, assets and API both requested, no console error but the write warning's designed 409. Fails when "Profile anyway" is relabelled | PASS |
| AC-36: the leaks row records what's built and tested; ADRs record the sampling method, the bundled extension and the credential store | Read: "AC-36, read (step 4)" above, each document against the code it names; the row updated in `ba7c8d0` | PASS |

Checks run, all on the tree above:
- `uv run --frozen pytest tests/integration tests/golden`: 2623 passed, 8 skipped (as many as on main and in DRIVN-56's run; which ones wasn't listed again), exit 0, in 8 min 58 s.
- `uv run --frozen pytest tests/e2e`: 87 passed, exit 0, in 3 min 48 s.
- `pnpm --dir apps/web test`: 263 passed, exit 0. Nothing in `apps/web` changed on this branch, so `tsc` and `vite build` weren't run again.
- CI's scans: detect-secrets over every tracked file, exit 0; pip-audit and `pnpm audit`: no known vulnerabilities. No dependency changed on this branch.
- `.ai/hooks/run.py pre-commit`: passed on each commit.
- The fixture afterwards: `log_line_prefix` back to `%m [%p] `, no role settings left, the journey's role dropped.
- `ai-skills knowledge discover`: `adr-index.md` takes ADR 0009's new hash (changed by DRIVN-56). `graph.yaml` and `facts.yaml` aren't kept from it, as PR #32 rebuilds both; `knowledge check` still reports them until it merges.

Not verified: CI on GitHub, where the Compose path of `server_log_from_now` (Linux) and a Windows server's log run for the first time.

## Review (2026-11-23, by the independent reviewer, on `7271df6`)
Verdict: ready after fixes. Each finding was reproduced before acting on it, and each fix driven by a test seen failing.
1. The AC-15 allow-list took in writes (risk): `SELECT … INTO`, which makes a table, and a row lock (`FOR UPDATE`,
   `FOR NO KEY UPDATE`, `FOR SHARE`, `FOR KEY SHARE`) matched the catalog's form, and a `COPY … TO` a server file,
   split at the `;` in its literal, was two parts of read forms. Fixed: a statement holding `INTO` or a row lock fails
   whatever its form, and a message splits only at a `;` outside a literal. The comment and the threat register's
   AC-15 sentence now say what the check proves: the forms seen, with what a function a read calls left to AC-14.
2. An entry without the test's prefix was skipped (risk): with the prefix put back mid-run, as by another
   checkout's test on a shared local fixture, a write in it went unchecked. Fixed: every entry read must carry the
   prefix, which the server's own lines carry too while it's set.
3. The test that a role not logged leaves nothing passed on an empty read (nit): it now sends the logged role's
   statement after, and needs it in the log. Seen failing with `server_log_from_now` reading nothing.
4. The journey didn't check the filled-in connection form (nit): it does, before Save, 11 states in all.
5. `state: ready` with the Verification filled (question): it moves to `done` at delivery, as DRIVN-56's did.

The tests of the check (`not_drivnxs_reads`) are in `tests/e2e/test_postgres_journey.py`: a log as DrivnX's reads
appear passes, and each case above fails by name.
