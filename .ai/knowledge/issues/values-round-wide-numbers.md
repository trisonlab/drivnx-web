---
id: values-round-wide-numbers
kind: issue
status: CONFIRMED
found: "2026-11-27"
related: [DRIVN-73, DRIVN-64, DRIVN-60, ADR-0004]
evidence:
  - packages/profiling/on_request.py
  - apps/local-api/local_api/profile_jobs.py
  - tests/integration/test_exact_value_range.py
  - tests/integration/test_postgres_values_api.py
---

# Values on request round a wide decimal and a nanosecond timestamp

**Symptom.** In DRIVN-64's review, a Parquet file with a `DECIMAL(38,2)` column holding 12345678901234567890123.45
and 12345678901234567890123.46 gave a minimum and a maximum of 1.2345678901234568e+22 each, with one histogram bucket
from 1.2345678901234568e+22 to 1.23456789012346e+22. Neither is a value in the file, and the two values look equal.
Its most frequent values were exact. A `TIMESTAMP_NS` column holding 2026-01-01 00:00:00.123456789 and …999 gave
00:00:00.123456 for both. Reproduced by calling `load_rows` and `values_for` exactly as `parquet_values_of` does
(CONFIRMED on DuckDB 2.0.0).

**Cause.** `values_for` types a column by its `normalised_type`: `number` through `TRY_CAST(cell AS DOUBLE)`, which
holds about 15 significant digits, and `timestamp` through `TRY_CAST(cell AS TIMESTAMP)`, which holds microseconds.
The most frequent values group the text itself, so they stay exact.

**Scope.** A decimal of at most 15 significant digits, and a microsecond timestamp, were unaffected. A CSV `number` and
a PostgreSQL `numeric` went through the same casts and rounded the same way: CONFIRMED in DRIVN-73, whose CSV and
`numeric(38,2)` tests fail with the fix reverted. Parquet makes it more likely, as a file keeps its native types.

**Fix.** DRIVN-73. `values_for` reads a number column as `DECIMAL(38, places)`, the places being the most the column
writes, when every number is written plainly and fits in 38 digits; otherwise as a double, as before. Its median is
halfway between the two middle values (`quantile_disc` both ways), averaged in Python's `Decimal`, as `quantile_cont`
over a decimal interpolates through a double. Its histogram's edges are worked out with `Decimal` too, and each value
counted by comparing it, in the column's decimal, with the edge rounded down to its places, as DuckDB's `histogram()`
compares decimals as doubles. A timestamp column is read as `TIMESTAMP_NS` when every time fits in it (1677 to 2262),
with its range and edges as whole nanoseconds; otherwise in microseconds, as before. Still rounded: the mean and
standard deviation (doubles), and a CSV time read with a format DuckDB sniffed (`try_strptime` gives microseconds;
INFERRED, not tested). A whole number column now shows `1`, not `1.0`, and a CSV column's range takes its longest
number's places (`7.000` beside `…567.891`), as a `DECIMAL(38,3)` from Parquet or PostgreSQL shows it; the user chose
that over the cell's own text on 2026-11-28. Values on 5 million rows of a full run take 1.0 to 1.1 s, against 0.43
to 0.45 s before (measured 2026-11-28 in the same run).
