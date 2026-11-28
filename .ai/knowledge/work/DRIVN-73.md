---
id: DRIVN-73
title: "Values on request keep a wide number's and a precise time's exact range"
feature: DRIVN-60
feature_revision: 1
stories: [US-03]
criteria: [AC-14]
blocked_by: [DRIVN-64]
state: done
external_key: DRIVN-73
status: CONFIRMED
---

# Values on request keep a wide number's and a precise time's exact range

Plane: [DRIVN-73](https://app.plane.so/drivnx/browse/DRIVN-73/)

## Outcome
A column's values on request show the minimum, maximum, median and histogram of a decimal wider than 15 significant digits, or of a timestamp finer than a microsecond, as they are in the file, not rounded through a double or to microseconds. Today they disagree with the same response's most frequent values, which are exact (`issues/values-round-wide-numbers.md`).

## Acceptance checklist
- [x] AC-14 (US-03, its values part): Given a completed run of a Parquet file with a `DECIMAL(38,2)` column holding 12345678901234567890123.45 and 12345678901234567890123.46, and a `TIMESTAMP_NS` column holding 2026-01-01 00:00:00.123456789 and 2026-01-01 00:00:00.123456999, when the user shows each column's values, then the minimum and maximum are those values as written, and the histogram's buckets hold values from the file.

## Expected evidence
Integration tests for the Parquet columns above, failing first. A CSV number of 20 or more significant digits and a PostgreSQL `numeric(38,2)` column checked the same way: fixed if `values_for` rounds them too, or recorded as not affected.

## Notes
Found in DRIVN-64's review (2026-11-27). The rounding is in the shared `values_for` (`packages/profiling/on_request.py`), which types a column by its `normalised_type`: `number` through `TRY_CAST(… AS DOUBLE)`, `timestamp` through `TRY_CAST(… AS TIMESTAMP)`. So CSV and PostgreSQL values most likely round the same way (INFERRED, not probed). A histogram's bucket edges are worked out in floating point (`_EDGE`), which a fix has to account for. Recorded and ticketed rather than fixed in DRIVN-64, as the user chose on 2026-11-27.

## Blocked by
DRIVN-64.

## Verification (2026-11-28, on `feat/drivn-73-exact-value-range` from `845f478`, uncommitted, after the review)
| Criterion | Evidence | Result |
|---|---|---|
| AC-14: a `DECIMAL(38,2)` column's minimum and maximum are 12345678901234567890123.45 and …46 as written | `test_exact_value_range.py::test_a_wide_decimals_minimum_and_maximum_are_as_written`; failed first with `1.2345678901234568e+22`. `test_the_widest_decimal_38_2_keeps_its_range`: 36 digits before the point, the most `DECIMAL(38,2)` holds (failed first: read as a double) | PASS |
| AC-14: its histogram's buckets hold values from the file | `test_a_wide_decimals_histogram_buckets_hold_values_from_the_file`: ten buckets 0.001 wide from …45 to …46, edges worked out by hand, one value in the first and one in the last; failed first with one bucket of both, ending at `1.23456789012346e+22`. `test_a_wide_decimals_median_is_exact`: …455, halfway (failed first: `1.2345678901234568e+22`). `test_the_widest_decimal_38_2_keeps_its_histogram_and_median`: 39-digit edges and median, one more than a decimal holds (fails with the comparison at one place more) | PASS |
| AC-14: a `TIMESTAMP_NS` column's minimum and maximum are 00:00:00.123456789 and …999 as written | `test_a_nanosecond_times_minimum_and_maximum_are_as_written`; failed first with `…00.123456` | PASS |
| AC-14: its histogram's buckets hold times from the file | `test_a_nanosecond_times_histogram_buckets_hold_times_from_the_file`: ten buckets 21 ns wide, worked out by hand (failed first: edges cut to microseconds, both values outside them). `test_a_nanosecond_times_median_is_a_time_from_the_file` (failed first: `…00.123456`). `test_times_beyond_what_nanoseconds_reach_keep_their_range`: 1500 and 9999 keep their range (failed against the first nanosecond fix with `(None, None)`) | PASS |
| Expected evidence: a CSV number of 20 or more significant digits | `test_a_csv_number_of_more_digits_than_a_double_holds_keeps_them`: 20 digits, range, median and buckets exact. Passed at once (shared code); fails with the decimal path switched off | PASS (affected, fixed) |
| Expected evidence: a PostgreSQL `numeric(38,2)` column | `test_postgres_values_api.py::test_a_wide_numerics_range_and_median_are_as_the_database_has_them`: range, median and buckets exact. Passed at once; fails with the decimal path switched off, which confirms it rounded before | PASS (affected, fixed) |

The review (reviewer agent, 2026-11-28) found two bugs in the first build, both fixed test-first:
- A decimal of 28 or more significant digits raised `decimal.InvalidOperation` (Python's default precision), and the
  values request failed where `main` had shown rounded values. Fixed with a 76-digit context, and a column now takes
  the decimal path whenever it fits in 38 digits.
- `quantile_cont` over a decimal interpolates through a double: `556269653869031489` and `786674504917291237` had a
  median of `671472079393161357.8`. The median is now halfway between the two middle values (`quantile_disc` both
  ways), averaged in Python's `Decimal`. `test_a_median_between_two_distant_middle_values_is_exact` (`…363`, by hand)
  passed at once, as the first fix had to replace the median too; it fails with the old median, with that value.
- `test_a_value_on_a_buckets_edge_counts_in_the_bucket_it_ends`: 0.2 on an edge of 0.18/0.2 counts in the bucket it
  ends. Passed at once; fails with the edges rounded up instead of down.

With `on_request.py` put back to `main`'s, 13 of the 14 tests in `test_exact_value_range.py` fail (after the
re-review's fix), and the PostgreSQL `numeric(38,2)` test fails too (checked before it); the one that passes is the
1500/9999 test, which guards the fallback.

Decided in building it (2026-11-27, 2026-11-28): a number column is read as `DECIMAL(38, places)`, its places being
the most its numbers write, only when every number is written plainly and fits in 38 digits; otherwise as a double,
as before. A timestamp column is read in nanoseconds only when every time fits in them (1677 to 2262). Two DRIVN-9
AC-30 assertions changed with it: a column of `1` and `3` shows `1`/`3` and a median of `2`, not `1.0`/`3.0`/`2.0`. A
CSV column's range takes its longest number's places (`7.000` beside `…567.891`), as a `DECIMAL(38,3)` from Parquet or
PostgreSQL shows it: the user chose that over the cell's own text on 2026-11-28.

The re-review (2026-11-28) found findings 1 to 5 resolved and two nits, both fixed on the user's choice: a
`DECIMAL(38,38)` column's edges and median are written as DuckDB writes its range, with no 0 before the point
(`test_a_decimal_of_38_places_writes_its_edges_and_median_as_its_range`, failed first with `0.555…`), and
`issues/values-round-wide-numbers.md` gives the new timing.

Checks run, on the tree above:
- `uv run --frozen pytest tests/integration tests/golden`: first 2 failed, 2725 passed, 8 skipped. Both failures were
  in `test_postgres_fixture.py`, reading the fixture's server log as `''`: Docker answered `_compose_runs_linux()`
  (another project's container had just started), so the log was read from Compose while the fixture's own server
  ran. Both pass with a `docker` that fails first on `PATH` (2 passed). Rerun the same way after the re-review's fix:
  2728 passed, 8 skipped, exit 0, in 9 min 13 s, with the PostgreSQL fixture from the local PostgreSQL 18.6
  (`PGBIN`); CI pins 18.7.
- `uv run --frozen pytest tests/e2e` (same `PATH`, same tree): 100 passed, exit 0, in 3 min 58 s.
- `pnpm --dir apps/web test`: 273 passed. `pnpm --dir apps/web exec tsc --noEmit`: exit 0 (no web code changed).
- Timing: `values_for` on a 5-million-row number column, every row: 1.0 to 1.1 s, against 0.43 to 0.45 s on `main`,
  in the same run. A fast run reads at most 100,000 rows.

Not verified / known limits:
- The mean and standard deviation are still doubles.
- A CSV time read with a format DuckDB sniffed goes through `try_strptime`, in microseconds (INFERRED, not tested).
- `ai-skills knowledge check`: `index/graph.yaml` predates the change; refreshed in its own PR, as after DRIVN-64.
- The fixture's log reader choosing Compose while its own server runs is outside this ticket (why the same probe
  saw no Docker in the 2026-11-27 run is INFERRED, not checked).
