---
id: reading-a-table-scan-between-chunks
kind: issue
status: CONFIRMED
found: "2026-11-23"
related: [DRIVN-53, DRIVN-45, ADR-0009]
evidence:
  - tests/integration/test_postgres_stopping.py
  - tests/integration/test_postgres_earlier_runs.py
  - packages/connectors/postgres.py
---

# A table's scan isn't `active` on the server between its chunks

**Symptom.** On main's CI on macOS (run 45098627084, `e112790`),
`test_cancelling_a_run_mid_read_stops_the_read_on_the_server[full-big]` failed with "The read never started on the
server" (`assert 0 == 1`), on a tree that had passed on PR #34. The file took 57 s against 52-54 s in passing runs,
so the 30 s wait hadn't timed out: it had seen the read.

**Cause.** The wait sampled `pg_stat_activity` once to end the loop and again to assert. A full profile reads a table
over one session (`SET threads = 1` in `load_whole_object`, `packages/connectors/postgres.py`), as `COPY` after `COPY`
of a range of pages, and the session isn't `active` between them. A probe sampling as the test does, while `big` was
read on this machine, saw 0s mid-read (`…1111110111…`): roughly one sample in a hundred. When the second look fell in
such a gap, it saw 0 while the read went on. A view's read is one long statement, so only a table's scan hits it.

**Fix.** `reading()` in `test_postgres_stopping.py` asserts on the count that ended its wait, not on a second look,
and the cancel test uses it; `test_postgres_earlier_runs.py` imports it for its `streaming` case on `big` too. Any new
wait for a read on the server must check what it saw, not ask again.
