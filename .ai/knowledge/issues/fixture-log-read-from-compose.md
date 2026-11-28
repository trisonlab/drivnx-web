---
id: fixture-log-read-from-compose
kind: issue
status: CONFIRMED
found: "2026-11-28"
related: [DRIVN-73, DRIVN-58, DRIVN-45]
evidence:
  - tests/postgres_fixture.py
  - tests/integration/test_postgres_fixture.py
---

# The PostgreSQL fixture reads its log from Compose whenever Docker answers, even while its own server runs

**Symptom.** During DRIVN-73's verification (2026-11-28, local Linux), a full
`uv run --frozen pytest tests/integration tests/golden` gave 2 failed, 2725 passed. The two were
`test_postgres_fixture.py::test_a_logged_roles_statements_are_in_the_servers_log_with_their_application` and
`::test_a_role_that_isnt_logged_leaves_no_statement_in_the_servers_log`, both reading the server's log as `''`.

**Cause.** `server_log_from_now()` (`tests/postgres_fixture.py`) chooses where to read by calling
`_compose_runs_linux()` when it's called, not by which server `start()` started. The fixture had been started as its
own server (`pg_ctl` from `PGBIN`, log at `<tmp>/drivnx-postgres-fixture/server.log`), and Docker with Compose now
answered on the machine (another project's container, `palloft-be-db-1`, had just started), so the log was read
through `docker compose logs`, which had nothing. CONFIRMED: with a `docker` that exits 1 first on `PATH`, both tests
pass (2 passed), and the whole suite then passed (2728 passed, 8 skipped).

**Not checked.** Why the same probe saw no Docker in the 2026-11-27 run that passed (INFERRED: Docker wasn't
reachable then). Whether `start()` itself goes wrong the same way, trying `compose up` while its own server holds the
port, wasn't tried.

**Workaround.** Run the suite with a stub `docker` that exits 1 first on `PATH`, or stop the fixture and start it
again so that it and its log reader agree. CI is unlikely to be affected (INFERRED): a runner's Docker doesn't change
between `start()` and the tests, and PR #52's run passed on all three systems.

**Fix (not done).** `server_log_from_now()` should read the log of the server that's running, e.g. the own server's
`server.log` whenever it exists, as `stop()` already prefers it. Worth a ticket if it happens again.
