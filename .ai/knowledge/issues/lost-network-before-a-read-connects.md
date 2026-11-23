---
id: lost-network-before-a-read-connects
kind: issue
status: CONFIRMED
found: "2026-11-23"
related: [DRIVN-53, DRIVN-45, ADR-0009]
evidence:
  - packages/connectors/postgres.py
  - tests/integration/test_postgres_stopping.py
---

# A read whose server stops answering before it connects was told "may have changed"

**Symptom.** On macOS CI (PR #35, run 45123965715, attempt 1),
`test_a_read_whose_network_fails_says_the_connection_was_lost[disable]` got reason `changed` ("It may have changed
since it was listed: refresh the list") instead of `lost`. A re-run passed.

**Cause, in DrivnX.** A read can open a session of its own after the attach. When the server or network is gone by
then, DuckDB raises `IO Error: Unable to connect to Postgres at "…": connection to server at "…" failed: Connection
refused`, which matched none of `_CONNECTION_LOST`, so `_not_read` fell through to `changed`. A server restarting
mid-run (DRIVN-45 AC-22) would say the same.

**Cause, in the test.** `late_sleepy` sends 100,000 rows, then sleeps 20 s. A drop during the sleep leaves the
server running the read, as nothing tells it the client is gone (the test's attach doesn't set
`client_connection_check_interval`). The next case's `reading()` matched that leftover, so its drop came before its
own read had connected. On Linux the proxy's closed listener went on accepting (its `accept` thread kept it open),
so the read reached the server anyway; on macOS it was refused. A probe on macOS's runner (draft PR #36, closed) saw
95 of 180 such drops classed `changed`; 80 of 80 on Linux were `lost`.

**Fix.** `_not_read` counts libpq's failed connection (`connection to server at`, as `_failure` reads it) as `lost`.
`DroppingProxy.drop` shuts its listener so every OS refuses afterwards, and the network test ends the read its drop
leaves on the server. `test_a_read_whose_server_stops_answering_before_it_connects_says_the_connection_was_lost`
drops before the read and failed with `'changed' == 'lost'` before the fix.
