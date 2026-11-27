---
id: DRIVN-73
title: "Values on request keep a wide number's and a precise time's exact range"
feature: DRIVN-60
feature_revision: 1
stories: [US-03]
criteria: [AC-14]
blocked_by: [DRIVN-64]
state: blocked
external_key: DRIVN-73
status: CONFIRMED
---

# Values on request keep a wide number's and a precise time's exact range

Plane: [DRIVN-73](https://app.plane.so/drivnx/browse/DRIVN-73/)

## Outcome
A column's values on request show the minimum, maximum, median and histogram of a decimal wider than 15 significant digits, or of a timestamp finer than a microsecond, as they are in the file, not rounded through a double or to microseconds. Today they disagree with the same response's most frequent values, which are exact (`issues/values-round-wide-numbers.md`).

## Acceptance checklist
- [ ] AC-14 (US-03, its values part): Given a completed run of a Parquet file with a `DECIMAL(38,2)` column holding 12345678901234567890123.45 and 12345678901234567890123.46, and a `TIMESTAMP_NS` column holding 2026-01-01 00:00:00.123456789 and 2026-01-01 00:00:00.123456999, when the user shows each column's values, then the minimum and maximum are those values as written, and the histogram's buckets hold values from the file.

## Expected evidence
Integration tests for the Parquet columns above, failing first. A CSV number of 20 or more significant digits and a PostgreSQL `numeric(38,2)` column checked the same way: fixed if `values_for` rounds them too, or recorded as not affected.

## Notes
Found in DRIVN-64's review (2026-11-27). The rounding is in the shared `values_for` (`packages/profiling/on_request.py`), which types a column by its `normalised_type`: `number` through `TRY_CAST(… AS DOUBLE)`, `timestamp` through `TRY_CAST(… AS TIMESTAMP)`. So CSV and PostgreSQL values most likely round the same way (INFERRED, not probed). A histogram's bucket edges are worked out in floating point (`_EDGE`), which a fix has to account for. Recorded and ticketed rather than fixed in DRIVN-64, as the user chose on 2026-11-27.

## Blocked by
DRIVN-64.
