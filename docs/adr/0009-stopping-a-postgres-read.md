# 0009 — A PostgreSQL read is stopped by DuckDB's interrupt, then by the server's cancel; a full profile reads PostgreSQL's text through the scanner

- **Status:** Accepted 2026-11-17
- **Date:** 2026-11-17
- **Source:** ticket DRIVN-53, the user's decisions in chat on 2026-11-17; spec `CFX-APP-postgres` revision 3 (AC-19 to
  AC-23, the Assumptions' masked patterns); amends ADR 0005 Decision 6

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

**3. A full profile reads each object whole through DuckDB's scanner, as PostgreSQL's text.** The connection has
`pg_numeric_as_varchar` and `pg_array_as_varchar` on, each column is cast to `VARCHAR` in DuckDB, and `bytea` is
written as `'\x' || lower(hex(...))`. The rows go into the job's local table and are measured there, as a CSV's full
profile is, since measuring sends several queries per column. A full run attaches with a `statement_timeout` of the
time limit and a margin, as a view's one statement can take longer than the 30 s other reads have.

**4. A full profile's masked patterns come from the object's fast reading.** Before the full read, the job reads the
object as a fast profile would (ADR 0005 Decisions 1 to 3) and keeps only its patterns, saved with scope `sample`.
Everything else is measured on every row, including copies of a row, as a CSV's full profile counts them.

**5. An object stopped at its time limit is a write-once observation,** `time_limit_reached` on its dataset, so the
run keeps ADR 0002's rules and needs no migration.

**6. A lost connection, a server restart and a revoked privilege are told apart.** The connector says which from the
server's English text, never quoting it: a statement timeout, the connection lost (as a restart's fast shutdown ends
every session the same way), or the account no longer allowed to read the object. Anything else still points to a
refresh.

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

Not measured: a network between DrivnX and the server, enums, ranges, domains and xml (DRIVN-54's golden fixture), and
a role in a cluster that restricts `pg_cancel_backend`.

## Consequences

- A database administrator sees one DrivnX session per job, and a second one for under a second when a read is
  stopped. AC-23 says so.
- A cancel or a time limit stops any read within about 1.5 s, and the server's work with it.
- A full profile does one extra fast read per object for its patterns, about 0.04–0.5 s on the fixture.
- If the server refuses the cancel, the read ends at its statement timeout, and the run still ends as asked.
- ADR 0005 Decision 6 holds for tables; for views and fast reads, Decision 1 here finishes it.
