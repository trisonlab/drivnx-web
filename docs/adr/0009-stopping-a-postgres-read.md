# 0009 — A PostgreSQL read is stopped by DuckDB's interrupt, then by the server's cancel; a full profile reads PostgreSQL's text through the scanner

- **Status:** Accepted 2026-11-17
- **Date:** 2026-11-17
- **Source:** ticket DRIVN-53, the user's decisions in chat on 2026-11-17; spec `DRIVN-45` revision 3 (AC-19 to
  AC-23, the Assumptions' masked patterns); amends ADR 0005 Decision 6. Decision 7 from ticket DRIVN-57 and the user's
  decisions in chat on 2026-11-22 (AC-33)

## Context

ADR 0005 Decision 6 said DrivnX enforces a full profile's per-table time limit itself, by timing each table and
interrupting the scan, as `statement_timeout` can't stop DuckDB's many short `COPY` statements. A cancel (AC-21) relies
on the same interrupt: `cancel_profile` sets the job's `stop` and calls `connection.interrupt()` every 50 ms until the
job ends (`apps/local-api/local_api/profile_jobs.py`).

Probes against the fixture on 2026-11-17 (Measured) found that the interrupt only stops a read DuckDB splits into
chunks, which is a table's scan. A view has no `ctid`, so the scanner reads it in one statement, and every fast-profile
read is one `postgres_query` (DRIVN-51). Neither returns before the server has sent every row. A cancel during a slow
view, or during a fast read over a slow network, would wait for that read, against AC-21's 5 s and AC-20's "stops
within 5 s, no query from DrivnX is left running".

A full profile also has to measure the same text a fast profile does. The fast profile reads each value as
PostgreSQL's `::text` (DRIVN-51), so `1.0` and `1.00` in an unscaled `numeric` are two distinct values. The scanner
turns an unscaled `numeric` into a `DOUBLE`, and arrays and `bytea` into DuckDB's own text.

## Decision

**1. A read is stopped in two steps.** DrivnX interrupts the job's DuckDB connection, as before. If the read hasn't
returned after 1 s, DrivnX opens a second connection with the same details and password, sends
`pg_cancel_backend(<pid>)` for the job's session, and closes it. The pid is the job's own, from `pg_backend_pid()`
after it attaches. The job's session stays open and reads the next object. Both a cancel (AC-21) and a time limit
(AC-20) stop a read this way, on a fast profile and a full one.

**2. The job reads over one session.** It attaches with `pg_pool_max_connections = 1` and
`pg_pool_acquire_mode = 'wait'`, and loads with `threads = 1`, so the pid it holds is the session that reads.
Stopping a read is the only time a second DrivnX session appears, for under a second. AC-23 says so from revision 3.

**3. A full profile reads each object whole through DuckDB's scanner, as PostgreSQL's text.** `pg_numeric_as_varchar`
and `pg_array_as_varchar` are on for that read only, and reset after it: with the array setting on, `postgres_query`
returns arrays as text too, and discovery's key columns came back as `{id}` (DRIVN-53, step 1). Each column is cast to
`VARCHAR` in DuckDB, and `bytea` is written as `'\x' || lower(hex(...))`. The rows go into the job's local table and are
measured there, as a CSV's full profile is, since measuring sends several queries per column. A full run attaches with a
`statement_timeout` of the time limit and a margin, as a view's one statement can take longer than the 30 s other reads
have.

**4. A full profile's masked patterns come from the object's fast reading.** Before the full read, the job reads the
object as a fast profile would (ADR 0005 Decisions 1 to 3) and keeps only its patterns, saved with scope `sample`,
and how many rows it read, `sample_rows_read`, so the patterns are labelled by how their rows were read (found in
DRIVN-53's review).
Everything else is measured on every row, including copies of a row, as a CSV's full profile counts them.

**5. An object stopped at its time limit is a write-once observation,** `time_limit_reached` on its dataset, so the
run keeps ADR 0002's rules and needs no migration.

**6. A lost connection, a server restart and a revoked privilege are told apart.** The connector says which from the
server's English text, never quoting it: a statement timeout, the connection lost (as a restart's fast shutdown ends
every session the same way), or the account no longer allowed to read the object. Anything else still points to a
refresh.

**7. A run's session has the server check that DrivnX is still there** (DRIVN-57, AC-33). A stop of DrivnX, Ctrl+C
or SIGTERM, cancels its read as in Decision 1, through `stop_jobs()` at shutdown. A kill can't: the server ends a read
that sends rows as soon as its socket closes, but one that computes before its rows runs to its end or its statement
timeout. So discovery checks with `set_config('client_connection_check_interval', '1s', true)`, after its catalog's
transaction, whether the server takes the setting, and if it does, the job's session starts with
`-c client_connection_check_interval=1s` in its options, which a session the extension opens again has too. PostgreSQL
takes it from version 14 on Linux, macOS, illumos and the BSDs, and refuses it on Windows, where a session with it
can't start; a Windows server stays as before. Preview, values and discovery don't set it: their statements end at the
30 s statement timeout. Rejected: ending DrivnX's sessions when it next starts, which needs the password and the network
before it's up; and attaching with the setting and again without it once refused, two connections to a Windows server
for every run.

## Alternatives considered

- **Interrupt only (ADR 0005 Decision 6 as written):** a view's read and every fast read ignore it. Rejected by the
  probes.
- **No second session, with a statement timeout per read:** bounds the time limit, but a cancel would wait for the
  read, up to 30 s for a fast read and the whole time limit for a view. AC-21's 5 s would have to go.
- **Profile only tables fully, not views:** leaves fast reads uncancellable and drops a kind of object the spec names.
- **A libpq cancel request instead of a second session:** it isn't a session, but the secret key it needs stays inside
  DuckDB's extension, and psycopg was rejected (spec, Decisions).
- **A full read through `postgres_query` with `::text`:** gives PostgreSQL's text directly and was faster (2.37 s
  against 3.48 s on 3 million rows), but can't be interrupted, and one statement meets the 30 s limit.
- **Patterns from the full read's first 100,000 rows:** no extra read, but ADR 0005 rejected first rows for tables, as
  they come from the oldest pages.
- **Recording a stopped object in the run's scope at its end, or in a new column:** the scope is written when the run
  starts, and a column needs a migration.

## Measured

On the fixture (PostgreSQL in Docker on the same Linux machine, DuckDB 2.0.0), 2026-11-17, with scripts kept outside
the repository:

| Read | Interrupted after 1 s: returned after | Server idle after | Sessions |
|---|---|---|---|
| Table, 3 M rows, scanner, `threads = 1` | 0.05 s | 0.09 s | 1 |
| Table, 3 M rows, scanner, default threads | 0.21 s | 0.22 s | 8 |
| Table, 3 M rows, `postgres_query` | 1.71 s (the read's end) | 1.72 s | 1 |
| `postgres_query`, 12 M generated rows | 30.39 s (the read's end) | — | 1 |
| `postgres_query` in each `pg_query_mode` | 5.43–9.26 s (the read's end) | — | 1 |
| View with `pg_sleep`, scanner | 7.3 s (the read's end) | 7.32 s | 1 |
| View of 6 M generated rows, scanner | 17.4 s (the read's end) | 17.41 s | 1 |
| View with `pg_sleep`, `pg_cancel_backend` from a second session as the fixture's own role | 0.32 s after the cancel | at once | 1 + 1 for the cancel |

- After each interrupt or cancel the job's session was idle and read the next query.
- `pg_statement_timeout_millis` set after the attach didn't reach the session: the view read for 8.3 s under a 1.5 s
  setting.
- A full read of the 3 M-row table took 2.37 s through `postgres_query` and 3.48 s through the scanner with
  `threads = 1`.
- With the two settings and the `bytea` cast, the scanner's text equalled `::text` for unscaled and `numeric(50,10)`,
  one- and two-dimensional arrays with quoted values and NULL, and `bytea`. Without them, `1.00` read as `1.0`, `{1,2}`
  as `[1, 2]` and `\xdeadbeef` as `\xDE\xAD\xBE\xEF`. Every other type tried was already equal: double, real, bigint,
  boolean, timestamp, timestamptz, date, time, timetz, interval, json, jsonb, uuid, money, inet, text and char.
  `timestamptz` only while DuckDB's time zone, the machine's, was the server's (UTC): in another, the scanner wrote it in
  DuckDB's. So for an object with a `timestamptz`, the whole read sets DuckDB's `TimeZone` to the server's, and writes
  one moment (2000-07-01 12:00 UTC) both ways; if DuckDB refuses the zone or writes it otherwise, the object fails,
  naming the zone (found in DRIVN-53's reviews, 2026-11-21). DuckDB refuses some zones PostgreSQL takes, `localtime`
  (where PostgreSQL uses the system's zone files, as Debian's does) and `<+03>-03`, and reads a POSIX zone's sign the
  other way: `UTC+3` is -03:00 to PostgreSQL and +03:00 to DuckDB.
  Named zones agree for that moment, UTC, Asia/Kolkata, Europe/Amsterdam and EST5EDT among them, but not before about
  1900, where PostgreSQL writes a local mean time's seconds: 1890-01-01 00:00 UTC is `05:21:10+05:21:10` in
  Asia/Kolkata and `05:21:10+05:21` in DuckDB, and in Europe/Amsterdam `00:19:32+00:19:32` and `00:17:30+00:17`.
  Those aren't detected.

For Decision 7, on 2026-11-22 against the fixture (PostgreSQL 17, Linux, no network), a process that read as a job does
was killed with SIGKILL, or SIGTERM, which a bare reader doesn't handle, and the fixture's `pg_stat_activity` watched
until nothing of DrivnX's was left:

| The read when the process ended | Without the check | `client_connection_check_interval = 500ms` |
|---|---|---|
| A table's whole read, 10 million rows, which sends as it reads | 0.01 s | 0.01 s |
| A view over `pg_sleep(20)` through `postgres_query`, which sends nothing until it ends | 19.7 s, its end | 0.19 s |

Set with `SET` through `postgres_execute` the check had no effect on the read: only a session's startup options did.

Not measured: a network between DrivnX and the server, enums, ranges, domains and xml (DRIVN-54's golden fixture), and
a role in a cluster that restricts `pg_cancel_backend`.

## Consequences

- A database administrator sees one DrivnX session per job, and a second one for under a second when a read is
  stopped. AC-23 says so.
- A cancel or a time limit stops any read within about 1.5 s, and the server's work with it.
- A full profile does one extra fast read per object for its patterns, about 0.04–0.5 s on the fixture.
- If the server refuses the cancel, the read ends at its statement timeout, and the run still ends as asked.
- ADR 0005 Decision 6 holds for tables; for views and fast reads, Decision 1 here finishes it.
- A DrivnX killed during a run leaves no read on a server that checks, within about a second; on a Windows server a
  read that computes before its rows stays until its statement timeout, on a full profile its time limit and 30 s.
  After a power loss or a lost network no close reaches the server, so such a read stays until its statement timeout
  on any server.
