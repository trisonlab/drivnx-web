---
id: CFX-APP-csv-profile-T05
title: "All the metrics"
feature: CFX-APP-csv-profile
feature_revision: 1
stories: [US-03, US-08]
criteria: [AC-14, AC-19, AC-20, AC-39]
blocked_by: [CFX-APP-csv-profile-T04]
state: blocked
external_key: null
status: CONFIRMED
---

# All the metrics

## Outcome
Every stored metric is computed and shown, including masked patterns, and golden files prove each value exactly.

## Acceptance checklist
- [ ] AC-14 (US-03): Given a completed run, then for each column the Profile tab shows row count read, missing count and percentage, distinct count and percentage, values not fitting the type, shortest, longest and average text length, and the most common masked patterns with counts. Numeric and date columns show the same set.
- [ ] AC-19 (US-03): Given each synthetic golden file, covering missing values, duplicates, dates, Unicode, mixed types and high cardinality, when it is profiled, then every saved metric equals its known expected value.
- [ ] AC-20 (US-03): Given Unicode text, then letters in any script become `A`, digits in any script become `9`, and other characters stay. Given a value longer than 40 characters, then its pattern is cut and marked as cut.
- [ ] AC-39 (US-08): Given a file with more columns than fit in one transaction's limit, when it is profiled, then all its observations are saved, in transactions of at most 1,000, and a review action finishes within `busy_timeout` during the run.

## Expected evidence
Golden tests in `tests/golden` with known expected metrics, a test of the masking across scripts, and a wide file proving the transaction limit.

## From planning
- Test these inputs: a file with a header and no rows, a completely empty column (percentages have nothing to divide by), and duplicate column names.
- Files that must be decoded in memory, have mixed line endings, have quotes first seen late, or have a chosen skip above uneven lines must read the same as in the preview. In T04, that last kind ends as `failed`.
- From T04: a row wider than the rest reads as an extra column with no saved field, so it gets no metric. AC-14 says "for each column", so this needs a decision here. The Profile tab says nothing about it yet.
- From T04: `measure_first_rows` uses columns by position through aliases, so a name from the file never reaches the SQL and `rowid` can't collide. New metrics should do the same. A `LIMIT` subquery runs out of memory on big cells where `WHERE rowid <` doesn't.
