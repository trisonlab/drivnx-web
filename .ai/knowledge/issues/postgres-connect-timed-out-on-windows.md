---
id: postgres-connect-timed-out-on-windows
kind: issue
status: UNKNOWN
found: "2026-11-26"
related: [DRIVN-62, DRIVN-45, DRIVN-52, time-limited-run-failed-on-windows]
evidence:
  - tests/integration/test_postgres_findings.py
  - packages/connectors/postgres.py
  - tests/postgres_fixture.py
---

# A connection to the fixture's PostgreSQL timed out once on Windows, cause unknown

**Symptom.** On `main`'s CI after #47 (run 45752400123, attempt 1, `215dd80`, Windows),
`test_postgres_findings.py::test_a_column_that_is_its_unique_constraint_gets_no_possible_identifier_finding` got
422 "The server didn't answer within 10 seconds. Check the host and port." when it started a fast run. It was the
module's second test; the other 10 passed. The rerun of the Windows job alone (attempt 2) passed, as did the same tree
on Windows in PR #47's run (`bed42cd`). Ubuntu and macOS passed in attempt 1. #47 changed only the Parquet preview, not
this path.

**What the timeout means.** `CONNECT_TIMEOUT_SECONDS` is 10, given to libpq as `connect_timeout=5` because DuckDB's
`ATTACH` tries twice (`packages/connectors/postgres.py`). So the runner's own PostgreSQL, on `127.0.0.1:54329`, didn't
complete a connection within 5 s twice in a row.

**Not the module's speed.** `test_postgres_findings.py` took about 20 s in attempt 1 and 18 s in attempt 2, so this
isn't the 28 s against 14 s slowdown of `time-limited-run-failed-on-windows`. Both issues are in the same module, on
Windows only.

**Lead (INFERRED, unchecked).** A slow moment of the fixture's server, which on Windows `tests/postgres_fixture.py`
starts itself with `pg_ctl` from `PGBIN` in the temporary folder, or connections left on it by an earlier module
holding it up. Nothing in the job's log shows which, and the server's own log went with the runner.

**Seen.** Only this once: none of the other 10 failed runs among the 60 latest CI runs had this message (checked
2026-11-26). If it happens again, give it a ticket. Keeping the fixture server's log as a CI artifact on failure
would show what the server was doing.
