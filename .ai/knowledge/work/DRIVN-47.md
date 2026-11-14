---
id: DRIVN-47
legacy_id: CFX-APP-postgres-T02
title: "Add a connection"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-01, US-03]
criteria: [AC-01, AC-02, AC-03, AC-05, AC-07, AC-14]
blocked_by: [DRIVN-46]
state: done
external_key: DRIVN-47
status: CONFIRMED
---

# Add a connection

Plane: [DRIVN-47](https://app.plane.so/drivnx/browse/DRIVN-47/) · `CFX-APP-postgres-T02`

## Outcome
A user adds a PostgreSQL connection in the Data Explorer, tests it, sees the server's version and whether it's encrypted, and finds it again after a restart, asked for the password then. Every session DrivnX opens is read-only.

## Acceptance checklist
- [x] AC-01 (US-01): Given the Data Explorer, when the user adds a PostgreSQL connection, then they enter host, port (5432 by default), database, user, password and TLS mode (`prefer` by default, `require`, or `verify-full` with a CA certificate file), and there's no field for a connection string or URL.
- [x] AC-02 (US-01): Given details that work, when the user tests or saves the connection, then DrivnX connects within the connect timeout and shows the server's version and whether the connection is encrypted, and a saved source appears in the Data Explorer under its database name and stays after a restart.
- [x] AC-03 (US-01): Given a host that can't be reached, refused credentials, a database that doesn't exist, or TLS that is required and fails, when the user tests or saves, then a specific message says which, within the connect timeout, and no source is saved.
- [x] AC-05 (US-01): Given no OS credential store, when a connection is saved, then the screen says the password is kept only until DrivnX stops, and after a restart opening the source asks for it before anything is read.
- [x] AC-07 (US-01): Given the installed wheel on each tested platform, when DrivnX connects, then it loads the PostgreSQL extension from inside the package with DuckDB's signature check on, and DuckDB makes no request to any host other than the database server. Given another platform, then the screen says PostgreSQL isn't available on it.
- [x] AC-14 (US-03): Given any connection DrivnX opens, then the session is read-only, and an attempt to write through it is refused by the server.

## Expected evidence
API and Playwright tests against the fixture for each field, each failure message within the connect timeout, and a restart. A write through DrivnX's connection refused by the server.

## Plan agreed with the user
Approved in chat, 2026-11-13, with one decision: **the fixture gets TLS**, so an encrypted connection and `verify-full`
are tested, not only their failures.

What the planning probe showed, against the fixture with DuckDB 2.0.0:
- **A password in the connection string is repeated in DuckDB's error text,** for a wrong password, a refused port, an
  unknown host, a missing database and TLS required alike. Given through a temporary DuckDB secret, it's in none of
  them, and `duckdb_secrets()` shows `password=redacted`.
- With `options=-c default_transaction_read_only=on`, the server refuses a write even through `postgres_execute`
  ("cannot execute CREATE TABLE in a read-only transaction"). Building step 3 showed DuckDB's `READ_ONLY` attach
  does the same on its own, as it opens read-only transactions; the planning note that it didn't was wrong, from a
  probe that had both layers on. DrivnX keeps both.
- The server reports its version, whether the connection is encrypted (`pg_stat_ssl`) and `application_name`. An
  unreachable host fails at 10.01 s with `connect_timeout=10`.

Approach:
- **Storage:** migration `0007` rebuilds `source`: `kind IN ('csv', 'postgres')`, the CSV columns nullable, new `host`,
  `port`, `database_name`, `user_name`, `tls_mode` and `ca_path`, a check that each kind has its own columns and not
  the other's, and `UNIQUE (host, port, database_name, user_name)`, so saving the same connection again returns it, as
  a CSV path does. A separate table was rejected: two writes and a join, and every listing changes anyway.
- **Password:** only in DrivnX's memory, keyed by source, never in SQLite; a `SecretStr` in the request models. A
  source's `password_kept` field says `until_stop`, and DRIVN-48 adds the credential store to it. `PGPASSWORD` (shared by
  the whole process) and a `passfile` (on disk) were rejected.
- **Connector:** `attach(connection, details, password)` creates a temporary secret, the only place the password goes,
  and attaches `READ_ONLY` with `sslmode`, `sslrootcert`, `connect_timeout=10`, `application_name=DrivnX` and the
  read-only `options`. `server_facts()` gives the version and encryption. A failure raises `ConnectFailure(reason)`:
  unreachable, credentials, no_database, tls, unavailable_platform or other. DuckDB's text is never re-raised or logged.
- **API:** `POST /api/sources/postgres/test`; `POST /api/sources/postgres` (tests, then saves);
  `POST /api/sources/{id}/password` after a restart; `kind` on every source in `GET /api/sources`. The CSV-only routes
  (rows, settings, starting a run) answer a database source with 409 and a message.
- **UI:** "Add a PostgreSQL database" in the Data Explorer opens the form below the CSV one. A saved source is listed
  under its database name. The centre shows the connection, the server's version, whether it's encrypted, the
  password note, and the password prompt after a restart. The Profile tab and Preview say "Not available yet" for it.

Steps:
1. **Migration and storage** (`0007_postgres_sources.py`, `artifacts/sources.py`): `StoredPostgresSource`,
   `add_postgres_source(engine, source) -> bool`, and listing and finding either kind. Verify: a workspace at 0006 with
   a CSV source, a run and observations migrates with every row kept; the checks refuse a postgres row with a path or
   no host; a saved source is there after reopening.
2. **TLS in the fixture** (`tests/postgres_fixture.py`, `testdata/postgres/compose.yaml`): a throwaway CA and server
   certificate made with openssl at start, and `ssl=on` on both paths. Verify on each path: `require` is encrypted.
3. **Connector** (`connectors/postgres.py`, and a role in `fixture.sql` whose password has `'`, `\`, a space and `é`).
   Verify against the fixture: the facts; each AC-03 failure's reason; a silent socket fails within 10 s and a margin;
   `verify-full` works with the CA and fails with another; the planted password is in no error and not in
   `duckdb_secrets()`; AC-14, a write refused by the server; `application_name`.
4. **API** (`sources_api.py`, `app.py` for the in-memory store, `profile_runs_api.py` for the 409s). Verify: test and
   save; each failure is 422 with its message and nothing saved; AC-07, no bundled extension gives a message naming the
   platform; after a restart the source says `password_needed` until a password is given; the CSV-only routes answer
   409; the password isn't in `workspace.db`, its WAL or the log at trace.
5. **UI** (`DataExplorer.tsx`, new `PostgresForm.tsx` and `PostgresDetails.tsx`, `Shell.tsx`, `api.ts` with `Source`
   a union on `kind`). Verify in Vitest: the fields and defaults, no connection string or URL field, the CA field only
   for `verify-full`, each message, the password note and the prompt after a restart.
6. **e2e** (`tests/e2e/test_connect_postgres.py`) against the installed wheel and the fixture: add, test (version,
   encryption), save (listed under `drivnx_fixture`), restart (asked for the password before anything is read), a
   wrong password's message, and an axe check on the new states.
7. **ADR 0007** on where the password goes, with the probe's evidence, for DRIVN-48 to extend with the credential
   store; this ticket; the index.

Risks: migration 0007 rebuilds `source` in users' workspaces, with the existing owner-only backup and step 1's test.
Failure reasons come from libpq's English messages, so a server in another language gets the general message, never
DuckDB's text. libpq reads `~/.pgpass` only without a password, which the form requires; under `require` it uses a
`~/.postgresql/root.crt` or client certificate if one exists, recorded as a limit.

Out of scope: the credential store (DRIVN-48), discovery and choosing (DRIVN-49), preview (DRIVN-50), profiling
(DRIVN-51 on), editing or removing a connection, and checking the platform before the form is filled.

## What building it showed
- **Step 1:** migration 0007 rebuilds `source` by SQLite's own procedure (new table, copy in `rowid` order, drop,
  rename); `dataset` refers to it by name, so nothing else changes. The migration test was seen failing with the
  copy losing `skip_lines` and `corrected`, and with it losing a source. Integration and golden: 2258 passed, 6
  skipped.
- **Step 2:** with TLS on, `prefer` always encrypts, so "not encrypted" and "TLS required and fails" need an account
  the server refuses over TLS: `drivnx_plain`, rejected by `hostssl` in `pg_hba.conf`. libpq's `prefer` then falls
  back to a plain connection, and `require` fails. Both paths read the one committed `pg_hba.conf`. The certificate
  names are in openssl configuration files, not `-subj`, which Git for Windows' shell rewrites.
- A test that runs `start()` with a stale folder made new certificates under the running server, and `verify-full`
  then failed with "certificate verify failed"; that test now uses its own folder. The Compose entrypoint (copying
  the key so the server's account owns it) was run with plain `docker run` on the pinned image, as Compose isn't
  installed here: `verify-full` encrypted, the plain account not, `require` refused for it.
- **Step 3:** DuckDB 2.0.0's `ATTACH` connects a second time whenever the first attempt fails, with the password
  inline or in a secret, and whatever its pool settings (`pg_pool_max_connections`, `pg_connection_cache`,
  `pg_pool_acquire_mode`, the health check). `psql` with the same settings made one connection. So a server that
  never answers took 20 s with `connect_timeout=10`, and a wrong password is two failed sign-ins in the server's log,
  which an account lockout would count twice. libpq now gets 5 s an attempt, so the user's 10 s holds (10.1 s,
  three runs); the double sign-in stays a limit for ADR 0007. `postgres_scan`, which might have connected once, is
  gone in DuckDB 2.0.
- Failure reasons are read from libpq's last attempt only: under `prefer` it tries again without TLS, and a wrong
  password for the plain account otherwise read as a TLS failure (seen with the whole text).
- Either read-only layer alone makes the server refuse a write; with both removed, the write tests fail. Each layer
  has its own test.
- A lone surrogate in the password makes DuckDB raise `RuntimeError('std::bad_cast')`, not a `duckdb.Error`, and a
  NUL is accepted but cut by libpq. Neither leaks the password; the API refuses both (step 4).
- **Step 4:** the API has no defaults: the form sends every field, and AC-01's defaults (5432, `prefer`) are the
  form's. Bad input is refused before any connection is tried. That's checked by the answer's shape, a list of
  problems: with only the status checked, 13 of 19 bad inputs "passed" because the connection they caused failed.
  Each failure's message reaches the user as the connector wrote it. Saving before testing was seen failing the
  nothing-saved checks. A password logged at debug was seen failing the scan of the logs at trace level.
- After a restart a source lists with `password_needed` and no server facts; a wrong password is refused and not
  kept, and the right one is. Rows, settings and starting a run answer a database source with 409 and a reason.
- **Step 5:** `Source` in `api.ts` is a union on `kind`; the CSV components take `CsvSource`, and the Shell shows
  `PostgresDetails`, an unavailable Preview and Profile, and no findings prompt for a database. Only
  `kind === "postgres"` is a database, so a source without `kind` still reads as a file. A CA path pasted with quotes
  was sent as typed (seen failing), and is now cleaned like a CSV path. Test and Save share one alert showing the last
  attempt's answer; without clearing the other, an earlier test's message stayed (seen failing). A validation answer
  is shown as the fields it names, in the form's words. Code written ahead of its test (sending `ca_path` only for
  `verify-full`, "isn't encrypted", the Profile branch, `aria-expanded`, showing the CA path) was checked by
  mutation: each broke a test. Vitest: 185 passed.
- **Step 6:** `tests/e2e/test_connect_postgres.py` drives the installed wheel against the fixture: testing (version,
  encrypted), a wrong password's message with nothing saved, saving and a restart that asks for the password before
  showing the server's facts, and `verify-full` with the fixture's CA. axe runs on the open form, the CA field, the
  failure, the saved source and the prompt. The behaviour was built already, so each test was seen failing by
  mutation instead: the server's encryption reported as off, saving without testing, no password prompt, the
  prompt's field unlabelled (axe's `label` rule) and the CA path not passed to libpq. e2e: 76 passed.
- **Step 7:** ADR 0007 records where the password goes, the two read-only layers, the failure messages and the probe's
  evidence, with the limits: two failed sign-ins for a wrong password, English-only reasons, and libpq's
  `~/.postgresql` files under `require` (from its documentation, not tested). ADR 0006 and the README say the fixture
  serves TLS and needs `openssl`. The threat register's credential row is partly implemented, and a new row, DrivnX
  changing a connected database, has its read-only sessions implemented. The knowledge index was rebuilt
  (`ai-skills knowledge check`: consistent).

## From the review
Each finding was reproduced before it was fixed, and each fix has a test seen failing first.
1. **Saving a saved connection with other TLS settings dropped them**: saved with `prefer`, then saved again with
   `verify-full` and the CA, the answer was 200 with `prefer` and no CA, and the server facts kept were from the
   `verify-full` test. The API now refuses other TLS settings with 409 and a message naming the saved mode, before
   connecting, and keeps nothing; the screen shows that message. Tests:
   `test_saving_a_saved_connection_with_other_tls_settings_is_refused_and_changes_nothing` (a stronger mode, a weaker
   one, another CA file) and its Vitest counterpart. The same check after losing a race to another save is covered
   only indirectly: with the first check removed, the tests pass through it.
2. **A test's result stayed after a field changed**, describing details no longer in the form. Changing a field now
   clears it. Test: "changing a detail after a test clears its result".
- Probed and sound: ten odd but valid passwords (quotes, `$$`, `;--`, a trailing backslash, combining marks, a
  right-to-left override, U+2028, a BOM, 1,000 characters) each gave the credentials message, with no leak.

## Verification
2026-11-14, on the working tree of `feat/drivn-47-add-a-connection`, with the fixture serving TLS from PostgreSQL 18's
binaries (`PGBIN`). No PostgreSQL test skipped: the only skips are the six by-design ones in `test_fast_profile.py`.

| Criterion | Evidence | Result |
|---|---|---|
| AC-01: host, port 5432, database, user, password, TLS mode `prefer`, `require` or `verify-full` with a CA file; no connection string or URL | `Postgres.test.tsx`: the fields and defaults, the exact list of fields, the CA field only for `verify-full` and sent only with it; `test_postgres_sources_api.py`: extra fields such as `url` and `dsn`, and hosts shaped like a URL or list, refused before connecting; e2e `test_verify_full_connects_with_the_servers_ca` | PASS |
| AC-02: tested or saved within the timeout, version and encryption shown, listed under the database name, kept after a restart | `test_postgres_connect.py` (version, encrypted, the plain account not); `test_postgres_sources.py` (kept after reopening); API test and save; e2e test, save and restart; Vitest for the screens | PASS |
| AC-03: unreachable, refused credentials, no database, TLS required and failing: a specific message within the timeout, nothing saved | `test_postgres_connect.py`: 8 failures by reason, a silent server failing within 10 s plus 2 (10.1 s measured); `test_postgres_sources_api.py`: each on both routes with nothing saved; e2e wrong password with nothing listed; Vitest for each message | PASS |
| AC-05: the screen says the password is kept until DrivnX stops; after a restart, asked for before anything is read | API restart test with real processes (`password_needed`, wrong password refused and not kept); e2e restart: the prompt, no server facts until the password; Vitest: the note, the prompt and that nothing about the source is fetched first | PASS |
| AC-07: the screen's half, PostgreSQL unavailable on another platform | API test of the exact message naming the platform; Vitest shows it. The wheel half is DRIVN-46's, and the e2e connects through the installed wheel | PASS on Linux, macOS and Windows (CI) |
| AC-14: read-only sessions, writes refused by the server | `test_postgres_connect.py`: three writes refused with "read-only transaction", each layer alone tested, the session's `default_transaction_read_only` is `on`, DuckDB's attach is read-only | PASS |

Checks run:
- `uv run --frozen pytest tests/integration tests/golden`: 2322 passed, 6 skipped. `uv run --frozen pytest tests/e2e`:
  76 passed. `pnpm --dir apps/web test`: 185 passed. After the review's fixes: 2325 passed, 6 skipped; e2e 76 passed;
  Vitest 187 passed. `tsc --noEmit` and `pnpm build`: passed.
- The documented secret scan over every tracked and new file: passed. pip-audit and pnpm audit: no known
  vulnerabilities. `uv lock --check`: no change; no dependency file changed. `.ai/hooks` pre-commit on every changed
  file: passed. `ai-skills knowledge check`: consistent.
- CI on 233997d (run 43565318440, PR #19): scans and tests passed on Ubuntu, macOS and Windows, each starting the
  fixture with `tests/postgres_fixture.py start` and `DRIVNX_REQUIRE_POSTGRES=1`: Compose on Ubuntu (container started),
  the platform's own server on macOS and Windows, with `openssl` making the TLS certificate on each. Integration and
  golden: Ubuntu 2325 passed, 6 skipped; macOS 2317 passed, 14 skipped; Windows 2259 passed, 72 skipped. Each is 81
  more passed than DRIVN-46's run with the same skips, so none of the new tests skipped. e2e: 76, 76, and 71 passed
  with 5 skipped on Windows, as on `main`; `test_verify_full_connects_with_the_servers_ca` passed on all three. Vitest
  187 passed on each.

## Blocked by
DRIVN-46, done (PR #18).
