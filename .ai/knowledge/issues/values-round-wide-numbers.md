---
id: values-round-wide-numbers
kind: issue
status: CONFIRMED
found: "2026-11-27"
related: [DRIVN-73, DRIVN-64, DRIVN-60, ADR-0004]
evidence:
  - packages/profiling/on_request.py
  - apps/local-api/local_api/profile_jobs.py
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

**Scope.** A decimal of at most 15 significant digits, and a microsecond timestamp, are unaffected. A CSV `number` and
a PostgreSQL `numeric` go through the same casts, so they most likely round the same way (INFERRED, not probed).
Parquet makes it more likely, as a file keeps its native types.

**Fix.** Not yet: DRIVN-73.
