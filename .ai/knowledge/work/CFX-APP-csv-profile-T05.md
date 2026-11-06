---
id: CFX-APP-csv-profile-T05
title: "All the metrics"
feature: CFX-APP-csv-profile
feature_revision: 2
stories: [US-03, US-08]
criteria: [AC-14, AC-19, AC-20, AC-39]
blocked_by: [CFX-APP-csv-profile-T04]
state: done
external_key: null
status: CONFIRMED
---

# All the metrics

## Outcome
Every stored metric is computed and shown, including masked patterns, and golden files prove each value exactly.

## Acceptance checklist
- [x] AC-14 (US-03): Given a completed run, then for each column the Profile tab shows row count read, missing count and percentage, distinct count and percentage, values not fitting the type, shortest, longest and average text length, and the most common masked patterns with counts. Numeric and date columns show the same set.
- [x] AC-19 (US-03): Given each synthetic golden file, covering missing values, duplicates, dates, Unicode, mixed types and high cardinality, when it is profiled, then every saved metric equals its known expected value.
- [x] AC-20 (US-03): Given Unicode text, then letters in any script become `A`, together with any combining marks after them, and digits and other numbers in any script, such as `½` and `Ⅷ`, become `9`. Punctuation, symbols, spaces and a mark with no letter before it stay. A character too new for DuckDB's Unicode tables becomes `A`. Given a value longer than 40 characters, then its pattern is cut and marked as cut.
- [x] AC-39 (US-08): Given a file with more columns than fit in one transaction's limit, when it is profiled, then all its observations are saved, in transactions of at most 1,000, and a review action finishes within `busy_timeout` during the run.

## Expected evidence
Golden tests in `tests/golden` with known expected metrics, a test of the masking across scripts, and a wide file proving the transaction limit.

## From planning
- Test these inputs: a file with a header and no rows, a completely empty column (percentages have nothing to divide by), and duplicate column names.
- Files that must be decoded in memory, have mixed line endings, have quotes first seen late, or have a chosen skip above uneven lines must read the same as in the preview. In T04, that last kind ends as `failed`.
- From T04: a row wider than the rest reads as an extra column with no saved field, so it gets no metric. AC-14 says "for each column", so this needs a decision here. The Profile tab says nothing about it yet.
- From T04: `measure_first_rows` uses columns by position through aliases, so a name from the file never reaches the SQL and `rowid` can't collide. New metrics should do the same. A `LIMIT` subquery runs out of memory on big cells where `WHERE rowid <` doesn't.

## Plan agreed with the user
- **Queries:** one aggregate query and one pattern query per column. `load_rows` names the temp table's columns by position, and returns the file's names. The plan's temp view was measured and dropped: every query had to expand all its aliases, at about 23 s for the aggregates on 1,500 columns against about 4 s.
- **Metrics:** `missing_percent` is a share of rows read. `distinct_count` and `distinct_percent` use exact text and non-missing values only, as a share of non-missing values. `not_fitting_count` counts non-missing values that don't fit the detected type. `shortest_length`, `longest_length` and `average_length` are code points of non-missing values, as written. Percentages and averages round to 2 places.
- **Nothing to divide by:** that observation isn't saved, and the screen shows "—".
- **Fitting the type:** integers must equal their value as a number, as `TRY_CAST('1.5' AS BIGINT)` gives 2. Dates use the format DuckDB sniffed, as a plain cast fails every value of a date like `03/25/2024`. Other types use `TRY_CAST`, chosen from the saved normalised type.
- **Masked patterns:** one `masked_patterns` observation per column, holding up to 10 of `{pattern, count, cut}`, with ties broken by pattern.
- **Decided by the user, taken from `/tdd` following the plan's recommendations:**
  - A surplus column from a wider row gets no metric, and the tab says so from a saved `extra_columns` count.
  - One table row per column, with "Measured on" and "Precision" on each row and patterns in an expandable cell.
  - Combining marks count as part of their letter, and non-decimal numbers like `½` and `Ⅷ` become `9`. ADR 0004 records this.
- **Build order:** lengths, distinct and percentages; values not fitting; masked patterns; reading the same as the preview, including a chosen skip above uneven lines; the screen; golden files; wide files and the transaction limit; surplus columns. Then the full suites, a real-browser probe, and a manual run on a 1 GB file on a real disk.

## What building it showed
- **The file T04 said would fail is narrower than it recorded.** A title line with a chosen skip reads fine. What reaches the uneven reader is a chosen skip that leaves uneven lines in the table, such as `title_line.csv` with 0 lines skipped. T04's note came from the plan and was never run. Those files now read as the preview does.
- **Very wide files take a while, and that's accepted.** One query per column cost about 41 s for the metrics on 1,500 columns of one row, plus about 11 s to save them. Batching 25 columns a query reached about 17 s, but gained little on a normal file (5 columns: about 0.7 s to 0.3 s) and needed a far more complicated pattern query, so it wasn't done. One query for all 1,500 used over 12 GB and failed.
- **Only the first 100 characters of a value are masked.** Masking 1,000 took 28 s on 100,000 cells of 1,000 characters, and 100 took 2.7 s for the same 40-character pattern. A value with several marks on every letter gets a shorter pattern, still marked as cut.
- **Timestamps have the date trap too.** `25/03/2024 10:00:00` is detected as `TIMESTAMP`, and a plain cast fails it, so both use the sniffed format.
- **Indicative only:** on a 1 GB, 26,120,000-row, 5-column UTF-8 file on this machine's NVMe disk, with the page cache dropped, three runs took 0.94 to 0.98 s with all 46 metrics, peaking at 246 MB. This is in-process with no browser. T12 measures the real product.
- **A screenshot found what tests couldn't.** With every metric in one table, the shared table style split words mid-word, and letting them keep their width made the whole page scroll. The Profile table now keeps whole words, and the centre column has `min-width: 0`, so the table scrolls inside its own box. T12 should check that the scrolling box can be reached by keyboard.
- **Labels stay next to the column.** "Measured on" and "Precision" come straight after the column name, so they're in view without scrolling, and "Patterns" is the part that scrolls.

## What verifying it showed
- **Valid dates counted as not fitting.** The format was sniffed strictly, which fails on files with mixed line endings or a short row, and the fallback of no format then rejected every non-ISO value. The sniff now uses the options that loaded the rows. A sniff that still fails counts as that way of reading failing, because carrying on would save wrong numbers. The one exception is a table with no rows, which has nothing to fit and which DuckDB can't sniff. A one-off sweep of every testdata file, with both header settings and automatic, 0 and 1 lines to skip, gave 211 completed runs and none failed, and no sniff raised an error. After the review's fixes and their 3 new golden files, the same sweep gave 229 completed runs, again with none failed.
- **CI didn't run the golden tests.** CI and the README only named `tests/integration` and `tests/e2e`. Both now run `tests/golden` too, and `test_ci.py` holds it.

## What reviewing it showed
- **Two formats of one type.** DuckDB detects one format per type, and still types a second column that its plain cast can read, so `2024-03-25` and `2024/03/25` are both dates. A value now fits if it matches the detected format or passes the plain cast, so an ISO date late in a column of `03/25/2024` fits too.
- **Large integers.** Comparing a value's two conversions to `DOUBLE` could round the same whole number differently, so about 1.4 to 2.8% of random values in the affected ranges counted as not fitting. A value now has to convert to `HUGEINT` and be whole as a `DOUBLE`. A value past `DOUBLE`'s precision, like `9007199254740993.5`, therefore still counts as fitting.
- **Characters too new for DuckDB.** Its tables don't have letters added in Unicode 15.1 and later, so asking for letters left them as written. A letter is now anything not known to be a mark, number, punctuation, symbol, separator or control character. A too-new digit therefore becomes `A`, not `9`.
- **Spaces in patterns.** They're now kept on screen, as ` 9` and `9 ` are separate patterns.
- **The spec's AC-20 wording lagged ADR 0004,** so revision 2 of the spec matches it, approved by the user on delivering T05.
- **For T07:** a late unterminated quote that loses rows, a wider last row, and the surplus note on a rewritten file are in that ticket.
