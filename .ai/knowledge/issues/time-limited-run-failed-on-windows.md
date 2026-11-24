---
id: time-limited-run-failed-on-windows
kind: issue
status: UNKNOWN
found: "2026-11-24"
related: [DRIVN-55, DRIVN-53, DRIVN-45, ADR-0009]
evidence:
  - tests/integration/test_postgres_findings.py
  - apps/local-api/local_api/profile_jobs.py
  - packages/connectors/postgres.py
---

# A time-limited database run ended `failed` once on Windows, cause unknown

**Symptom.** On PR #40's CI on Windows (run 45308993356, attempt 1, `c40513d`), which changed only an e2e test,
`test_an_object_stopped_at_its_time_limit_has_no_findings_and_the_others_do` saw its run of `gaps` then `late_gaps`
(full, a "minute" of 3 s) end `failed`, not `completed`. The re-run passed. The run's stored reason was lost with the
runner's workspace, as the test asserted only the status. No `PytestUnhandledThreadExceptionWarning`, so the job
caught one of the errors `_run_database_profile` stores a reason for. `test_postgres_findings.py` took 28.3 s against
14.0 s on the re-run, the largest slowdown of any file, in a run about 8% slower overall.

**Ruled out.** 100 runs of the test's flow on Linux (idle, and under twice as many CPU-bound loops as cores, with
limits of 0.02-0.5 s and across the 0.2-0.29 s where `gaps` is sometimes stopped) and 100 on `windows-latest`
(limits 0.25-3 s, a draft probe PR, #41, closed) all ended `completed`. On Windows `gaps` reads in about 0.1 s and
never reached its limit, and `late_gaps`' cancel on the server took about 0.25 s and always ended its read with the
handled `ConnectFailure`. So neither a late cancel nor a late interrupt after `gaps` was stopped explains it there,
and the failure needs something the full suite leaves behind.

**Lead (INFERRED).** The extra 14 s fits `late_gaps`' read being left on the server for its 30 s sleep, so that the
module's `DROP SCHEMA … CASCADE` waited for it: a read that failed before its limit isn't cancelled on the server,
and PostgreSQL on Windows doesn't notice DrivnX closed the connection (ADR 0009).

**Change.** The test's status assertion now shows the run's `failure`, which names the object (`<schema>.<name>: …`),
so the next failure says where it failed. A `duckdb.Error` is stored only as "DrivnX couldn't profile this object.",
so a failure of that kind will name the object but not the error.
