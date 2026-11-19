---
id: DRIVN-54
legacy_id: CFX-APP-postgres-T09
title: "Every metric against the golden fixture"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-05]
criteria: [AC-25]
blocked_by: [DRIVN-51]
state: done
external_key: DRIVN-54
status: CONFIRMED
---

# Every metric against the golden fixture

Plane: [DRIVN-54](https://app.plane.so/drivnx/browse/DRIVN-54/) · `CFX-APP-postgres-T09`

## Outcome
Every saved metric is right for PostgreSQL's types: dates and times with time zones, Unicode, numeric, boolean, JSON, UUID, arrays and `bytea`, on tables, a partitioned table, a view and a materialized view.

## Acceptance checklist
- [x] AC-25 (US-05): Given the synthetic fixture database, covering missing values, duplicates, dates and times with time zones, Unicode, numeric and decimal, boolean, JSON, UUID, arrays, `bytea`, high cardinality, a partitioned table, a view and a materialized view, when each object is profiled, then every saved metric equals its known expected value.

## Expected evidence
Golden tests on the fixture database, each expected value worked out independently of the code.

## Blocked by
DRIVN-51

## Verification
Commit `ecec99e`, 2026-11-19, Linux, with the PostgreSQL fixture (PostgreSQL 17).

| Criterion | Evidence | Result |
|---|---|---|
| AC-25 | `tests/golden/test_postgres_metrics.py` `test_every_saved_metric_equals_its_known_value`, one case per object: dates and times with time zones (`moments`), Unicode, `text`, `char` and `varchar` (`words`), numeric and decimal (`numbers`), boolean, JSON and UUID (`documents`), arrays and `bytea` (`collections`), missing values and duplicates (`repeats`), high cardinality, 100,000 values read whole (`many`), a partitioned table (`visits`), a materialized view (`visit_names`) and a view (`named_visits`, its first rows, scope `sample`). Every metric is compared whole, once each. Each object was seen failing with the code it guards broken: the text-only metrics given to every type, blank text missing, lengths in bytes, distinct values as numbers, JSON and UUID as text, `text[]` or no `varchar` as text, a duplicate-row count, a page sample of every table, a view read whole, every scope `full`, and percentages rounded to 1 place. The independent review recomputed every expected value with its own calculator on PostgreSQL 17.6 and 18.6: 0 mismatches. | PASS |

Checks: `pytest tests/integration tests/golden` 2504 passed, 8 skipped (Windows-only and by-design files) and
`pytest tests/e2e` 80 passed, at `8a1e308`; since then only the golden test changed, and `pytest tests/golden` passed
21 at `ecec99e`. `pnpm --dir apps/web test` 215 passed; `tsc --noEmit` clean; the pre-commit hook passed;
`detect-secrets`, `pip-audit` and `pnpm audit` clean; `ai-skills knowledge check` consistent. CI on `7aca4c0` passed
its tests on Linux (PostgreSQL 18.7), macOS and Windows; its secret scan took a UUID for a key, fixed in `ecec99e`.

## Decisions agreed with the user
Made in chat, 2026-11-18 and 19. Distinct values are counted on PostgreSQL's text form for each value, as DRIVN-51
agreed, so `1.0`, `1.00` and `1` in an unscaled numeric are three values, and so are `-0` and `0` in a real, which
PostgreSQL finds equal. High cardinality is 100,000 distinct values, never analysed so it's read whole. Each expected
percentage is written out, not computed. The objects are made by the test in its own schema, not in `fixture.sql`, as
the other PostgreSQL tests make theirs.

## Follow-up
A server whose `extra_float_digits` is 0 writes floats in fewer digits, so distinct counts of `real` and `double` can
fall (0.3 and 0.1 + 0.2 become one value). The connector sets no session value. Whether to pin it is for the user to
decide; it isn't part of AC-25.
