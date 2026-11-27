# 0010 — How a profile reads a Parquet file: seeded slices, one in each eighth, and the row count from its metadata

- **Status:** Accepted 2026-11-26
- **Date:** 2026-11-26
- **Source:** Spec `DRIVN-60` ("A Parquet file's fast profile reads about 100,000 rows from row groups chosen with the
  run's seed"); the user's decisions in chat on 2026-11-26, while building `DRIVN-63`; the research note
  `.ai/knowledge/dependencies/duckdb-xlsx-parquet.md`; ADRs 0004 and 0005

## Context

A CSV's fast profile reads its first 100,000 rows, as a random sample of a file still reads all of it (ADR 0004). A
database's is a page sample with a seed (ADR 0005). A Parquet file sits between the two: it's a file, but its footer says
how many rows it has and where each row group is, so DuckDB can read a few rows from anywhere without reading the rest.

The spec fixes the outcome, "about 100,000 rows from row groups chosen with the run's seed", labelled "about N rows,
sampled by row group", with the exact row count from the metadata (AC-12). It leaves open how the rows are chosen, and
what happens to a file with one huge row group (AC-30). Values on request are worked out again from the run's own rows
(ADR 0004, decision 2; DRIVN-64), so the sample must be repeatable.

## Decision

**1. A fast profile reads 8 slices of 12,500 rows, one in each eighth of the file.** The file's rows, as its metadata
counts them, are split into 8 equal stretches. In each, a slice starts at a row chosen with the run's seed (Python's
`random.Random(seed)`) and is 12,500 rows long, so 100,000 rows in all, from across the whole file. DuckDB reads them
with a filter on its own `file_row_number`, so it skips the row groups no slice is in. Results are labelled "about
100,000 rows, sampled by row group". (`connectors/parquet_files.py`, `sample_slices` and `load_rows`.)

**2. One huge row group is sampled in the same way.** The stretches don't depend on row groups, so a file of 5,000,000
rows in one row group still gives 8 slices inside it. This answers the spec's open question.

**3. A file of at most 100,000 rows is read whole,** labelled "every row, N rows" (AC-30).

**4. The run keeps its seed and its slices.** The seed is saved as for a database's run, and the slices, as `(first
row, rows)` pairs, in the run's scope. A later run with the same seed reads the same rows, and values on request read
the run's own slices, not ones worked out again, which a change to Python's random numbers could move.

**5. The row count comes from the file's metadata,** saved with each run as `row_count` with the scope `metadata`, and
shown as "This file has N rows, from the file's metadata." Statistics are never read (ADR 0004, threat register R3):
only `parquet_file_metadata`'s `num_rows`.

**6. A full profile reads every row, and takes its masked patterns from the slices a fast profile would read,** as a
database's full profile does (decided with the user for Parquet on 2026-11-26, after DRIVN-53's decision for a
database). The run saves `sample_rows_read` for them, and the patterns are labelled with the sample.

**7. The file is read on its own.** Every read passes `hive_partitioning = false`, so a folder named like `id=x` adds no
column. The file's columns are renamed by position, so `file_row_number` is DuckDB's even when the file has a column of
that name.

## Alternatives considered

- **Whole row groups chosen with the seed until there are 100,000 rows:** about as cheap, but the rows come from one or
  two places in the file (for a 40,000,000-row file in 122,880-row groups, one group), and a file with one huge row
  group needs a second rule. Rejected by the user in favour of slices spread over the file.
- **DuckDB's `USING SAMPLE` (reservoir, system or Bernoulli):** a true random sample, but it reads the whole file: 3.2
  to 4.2 s on 40,000,000 rows (the research note), against 0.04 to 0.09 s for slices.
- **The first 100,000 rows, as a CSV:** cheapest, but not random, and a Parquet file doesn't need it.
- **Seeding with DuckDB's `setseed`** and choosing rows in SQL: ties the sample to DuckDB's random numbers, which can
  change between versions, and is harder to keep in the run.
- **The full profile's patterns from every row:** possible, but masked patterns are kept to a sample (ADR 0004), and a
  database's full profile takes them from its fast reading.

## Measured

On 2026-11-26, on this machine's NVMe disk (8 cores, 15 GB, DuckDB 2.0.0 limited to 4 GB), with the page cache dropped
before each profile, through the real UI (`tests/measure_large_parquet.py`, three runs). A synthetic file of 1.02 GB,
24,705,359 rows in DuckDB's default row groups, 5 columns (`id`, an `md5` hash, one of 6 cities, an amount, a date).
Each time is from the click until the page shows the result, so a profile's includes up to 1 s of the page polling.

| Step | Target | Measured |
|---|---|---|
| Connect, metadata shown (AC-07) | 1 s | 0.216 to 0.252 s |
| Fast profile (8 slices) | 5 s | 1.352 to 1.365 s |
| Full profile (every row, and the slices for patterns) | 60 s | 15.7 to 32.2 s |
| Cancel 3 s into a full profile, until "cancelled" (AC-13) | 5 s | 1.401 to 1.421 s |

The full profile's spread is the disk and the machine's other work; the first run was the slowest.

## Consequences

- Values on request for a Parquet run (DRIVN-64) read the slices in the run's scope.
- A run started on a file whose row count has changed since it was connected plans its slices from the count read as
  the run starts.
- A file written with an unusual layout (row groups of very different sizes) is still sampled evenly by rows, not by
  row group; the label says "sampled by row group" because DuckDB reads only the row groups a slice is in.
