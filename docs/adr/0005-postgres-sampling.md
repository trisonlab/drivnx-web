# 0005 — How a profile reads a PostgreSQL table: page samples with a seed, one connection, DrivnX's own time limit

- **Status:** Accepted 2026-11-13
- **Date:** 2026-11-13
- **Source:** The user's decisions in chat on 2026-11-13, recorded in spec `CFX-APP-postgres`; spike 2 of the direction
  doc (Section 10, "Can we profile large remote tables without expensive full scans?"); the open "PostgreSQL sampling"
  decision in Section 11; ADR 0004

## Context

Section 5 asks for a fast profile that is "sample-based" and a full profile only on explicit request. Section 11 leaves the
sampling method open: random, deterministic or bounded query. A CSV's fast profile reads its first 100,000 rows, because a
random sample of a file still reads all of it (ADR 0004, spec `CFX-APP-csv-profile`). A database can sample without reading
everything, so the question is which way, and what it costs.

Values on request are computed again from the same rows as the run (ADR 0004, decision 2), so a sample must be repeatable.
Section 5 also asks for full scans to be cancellable with per-table limits.

## Decision

**1. A fast profile of a table, partitioned table or materialized view reads `TABLESAMPLE SYSTEM` with `REPEATABLE`.** The
percentage is 100,000 ÷ the server's row estimate (`pg_class.reltuples`), so it reads about 100,000 rows, picked by page
from across the whole table. The seed is saved in the run's scope. Results are labelled "about N rows, sampled by page".

**2. A small or never-analysed table is read whole, with a guard.** When the estimate is at most 100,000, or the table has
never been analysed, DrivnX reads at most 100,001 rows. If the guard is reached, the estimate was wrong, and the results are
labelled as the first rows returned, not random. Otherwise they're labelled as the whole table.

**3. A view reads the first 100,000 rows it returns,** labelled "first rows the view returned, not random", because
PostgreSQL refuses `TABLESAMPLE` on a view.

**4. Values on request use the run's method and seed.** They're the same rows only while the table hasn't changed since the
run, and the label says so. A view's rows may differ.

**5. A full profile reads every row through DuckDB's scanner, over one connection.** A database administrator sees one
DrivnX session, at the cost of a slower scan.

**6. DrivnX enforces the per-table time limit itself.** It times each table and interrupts the scan when the limit is
reached. `statement_timeout` can't do it, because DuckDB splits a scan into many short `COPY` statements.

## Alternatives considered

- **`LIMIT 100000`:** as cheap as a page sample, but it returned neither the first rows nor the same rows twice. Two runs on
  an unchanged table gave different rows, all from the oldest 30% of the table. Values on request couldn't re-read the run's
  rows. Kept only for views, where nothing else works.
- **`TABLESAMPLE BERNOULLI`:** samples row by row, which spreads better where similar rows sit together, but the server reads
  every page, so its cost grows with the table. Rejected for that cost.
- **DuckDB's `USING SAMPLE`:** samples after DuckDB has received every row, so it moves the whole table over the network.
- **The full profile's aggregates run inside PostgreSQL:** nothing crosses the network, but it took about 6 times as long and
  loads the server's CPU.
- **DuckDB's parallel connections for a full profile:** about 1.6 times as fast, but 7 sessions from a profiling tool on a
  shared database can worry its owner. Rejected by the user.
- **Detecting that a table changed since a run** (from `pg_stat_user_tables` counters): more precise for values on request,
  but the counters can be reset and it adds a statistic to test. Rejected by the user in favour of the label.

## Measured

Spike 2, 2026-11-13. PostgreSQL 18.6 on the same machine over loopback, on an NVMe disk. DuckDB 2.0.0 with its postgres
extension (version `c0f5a3c0c4`), attached `READ_ONLY`. A synthetic table of 10,000,000 rows and 5 columns, 790 MB, whose
first 30% of rows hold different cities from the rest. Two runs of the script, each timing three times unless stated, and
the ranges cover both. Script: `tests/measure_postgres_sample.py`.

| Fast profile sample, aiming at 100,000 rows | Load (s) | Rows | Share from the first 30% | Cities seen (of 7) | Same rows twice |
|---|---|---|---|---|---|
| `LIMIT 100000` | 0.040–0.047 | 100,000 | 100% | 3 | no, even unchanged |
| `TABLESAMPLE SYSTEM` + `REPEATABLE (42)` | 0.034–0.039 | 95,560–95,566 | 27.7% | 7 | yes, while unchanged |
| `TABLESAMPLE BERNOULLI` + `REPEATABLE (42)` | 0.266–0.352 | 99,365–99,366 | 29.9% | 7 | yes, while unchanged |
| DuckDB `USING SAMPLE` (one per run) | 2.058–2.089 | 100,000 | 29.8–30.0% | 7 | no |

- Profiling the sample in DuckDB took 0.38–0.44 s, whichever method made it.
- Updating 1,000 rows changed `BERNOULLI`'s sample in both runs. It changed `SYSTEM`'s in the first run and not in the
  second, probably because the updated rows stayed on their pages (INFERRED). A changed table may or may not change a sample.
- `TABLESAMPLE` worked on the materialized view and the partitioned table, and was refused on the view. `LIMIT` on the view
  took 0.046–0.056 s.
- Full profile, every column in one pass: 2.5–2.8 s through DuckDB's scanner with its default parallel connections (7 and 8
  server sessions were seen), 4.03–4.22 s with one thread, and 15.3–16.5 s with the same aggregates run inside PostgreSQL.
  The duplicate-row check took 3.1–3.6 s.
- Cancelling 2 s into a full scan: DuckDB returned in 0.096–0.136 s, and no DrivnX query was left on the server after
  0.123–0.167 s.
- `statement_timeout` of 2 s reached the server, but the scan finished, in 3.26 s on the second run. The first run's server
  log showed 987 `COPY` statements, each under the limit.
- Discovery from the catalog: 1,004 objects in 3–4 ms, 3,024 columns in 9 ms, 2,111 keys in 2–4 ms.
- Read-only: DuckDB refused `INSERT` and `CREATE` on the read-only attach, and opened every transaction as
  `REPEATABLE READ READ ONLY`, so the server refused SQL sent straight through `postgres_execute` as well.

**Not measured:** a cold disk (the OS cache couldn't be cleared without root), a network between DrivnX and the server, other
PostgreSQL versions and wider tables. A full scan over a network is bounded by moving the table: 790 MB is about 6 s at
1 Gbit/s and about 63 s at 100 Mbit/s, by arithmetic.

## Consequences

- A page sample takes whole pages, so rows stored together come together, and its size varies (95,566 for 100,000). The
  label says "about" and "by page".
- The seed is part of a run's scope, so values on request can repeat the sample.
- A full profile over one connection is slower than DuckDB's default, and over a slow network the table's size dominates.
  The per-table time limit bounds it.
- Section 5's "sample by default" holds for tables. Views are the exception, like a CSV's first rows.
