---
id: DRIVN-30
legacy_id: CFX-APP-csv-profile-T10
title: "Values on request"
feature: CFX-APP-csv-profile
feature_revision: 4
stories: [US-05, US-06]
criteria: [AC-29, AC-30, AC-31, AC-32, AC-34]
blocked_by: [DRIVN-26, DRIVN-29]
state: done
external_key: DRIVN-30
status: CONFIRMED
---

# Values on request

Plane: [DRIVN-30](https://app.plane.so/drivnx/browse/DRIVN-30/) · was `CFX-APP-csv-profile-T10`

## Outcome
A user can ask to see a column's real values and statistics, computed fresh and never saved, with an extra confirmation for columns flagged as possibly sensitive.

## Acceptance checklist
- [x] AC-29 (US-05): Given a column flagged as possibly sensitive, when on-request values are requested without the extra confirmation, then the API refuses and shows no values. With the confirmation, they are shown.
- [x] AC-30 (US-06): Given a column in a completed run, when the user asks to see its values, then min, max and the 10 most frequent values are shown, and for numeric and date columns also a histogram, and mean, median and standard deviation where they apply. They are labelled with the part of the file they came from and the time they were computed.
- [x] AC-31 (US-06): Given values shown on request, then each is cut at 1,000 characters and marked when cut, as in the preview, and the response is `Cache-Control: no-store`.
- [x] AC-32 (US-06): Given a file that is no longer readable, when values are requested, then the user gets a clear message, and the saved metrics still show.
- [x] AC-34 (US-06): Given every new GET route, when it is called, then no workspace data changes, and it follows AC-08, AC-09 and AC-13 of the first slice.

## Expected evidence
Tests naming each criterion, including `Cache-Control: no-store`, the refusal without confirmation, and the first slice's checks on the new GET route.

## Plan agreed with the user
Decided by the user, 2026-11-07, taking every recommendation:
- **`GET /api/profile-runs/{run_id}/values?column=<name>`**, with `confirmed=true` for a flagged column. Values belong to a completed run, and its mode decides the part of the file read: the first 100,000 rows after a fast profile, the whole file after a full one. They're read as the job reads them (`load_rows` into an in-memory DuckDB table, 4 GB, spill inside the workspace), and nothing is saved or cached (ADR 0004).
- **Refused with 409 and a message:** a run that isn't completed, a run made with earlier settings (T11 can offer them later), a file that can't be found or read (AC-32), columns that no longer match, a flagged column without the confirmation (AC-29), and a request while a profile runs or other values are being computed, as two DuckDB jobs could take 8 GB.
- **Flagged** means the run has a possibly-sensitive finding citing the column, decided by the server.
- **What's given:** min, max and the 10 most frequent values with counts for every column. Number and date columns also take min and max in their own order, over values that fit, and a histogram of 10 equal-width buckets. Numbers get mean, median and standard deviation, and dates the median only. Each value is text, cut at 1,000 characters and marked when cut, with the part of the file read and the time computed. `Cache-Control: no-store` comes from `guard.py`.
- **On screen:** a "Show values" button on each column row of the Profile tab opens a section below the table. A flagged column asks "This column may hold personal data. Show its values?" first. A failure shows its message there, and the metrics stay. Values aren't kept once the section closes.
- **Target:** as for profiles, at most 5 s for the first rows and 60 s for the whole file, measured on 1 GB. A miss is reported, not redesigned.

Build order:
0. `columns_now` moves out of `start_run`, so both routes use it.
1. `values_for(...)` in `packages/profiling/on_request.py`, tested on synthetic tables.
2. The route, through real fast and full runs (AC-29 to AC-32).
3. AC-34: the GET snapshot, AC-08 and AC-09 tests, and nothing created in the current directory.
4. The web client, button, confirmation and values section, with Vitest.
5. Playwright: a flagged column's values after confirming, on the installed wheel.
6. The 1 GB measurement and the full suites.

Out of scope: values for earlier runs and "the file changed since this run" (T11), the planted-values scan (AC-33, T12), a cache, and cancelling a values request.

## What building it showed
- **Values are worked out in `packages/profiling/on_request.py`** from the table `load_rows` fills, as the job's metrics are, with missing values left out as the profile leaves them. Number and date columns take their range in their own order over values that fit, with the date format DuckDB sniffed, so `03/25/2024` sorts as a date. A value that doesn't fit is still a frequent value.
- **Histogram edges come from DuckDB's `equi_width_bins(min, max, 10, false)`.** Its rounded "nice" edges gave 13 buckets for decimals, so they aren't used. A short integer range gives fewer than 10, as integers can't split finer. Decimal edges are printed to 15 significant digits, as 0.1 + 0.06 adds up to 0.1599999999999998.
- **A column named `name` is flagged by T09's rules,** so tests that mean an unflagged column use `city`.
- **The read-error mapping is shared** by starting a run and asking for values (`_reading` in `profile_runs_api.py`), and now also turns a DuckDB error at the start into "DrivnX couldn't read this file." instead of a 500.
- **Every row of the Profile tab has a "Show values" button,** so an e2e test that picked the `value` column's row by its text matched every row. It now picks the row by its header.
- **Measured on 2026-11-07** through the API in process, on the NVMe disk, on a 1.0 GB, 5-column UTF-8 file of 26,120,000 rows, 3 tries per column: after a fast profile 0.19 to 0.22 s, after a full profile 10.51 to 13.21 s. The targets are 5 s and 60 s. The file had just been written, so the page cache may flatter the whole-file times.

## From reviewing T10
- **A number column holding `inf`, `NaN` or `-Infinity` gave no values at all,** only "DrivnX couldn't read this file.", as DuckDB's `equi_width_bins` refuses an infinite edge. Numbers and dates that aren't finite now take no part in the range, the statistics or the histogram, as a value that doesn't fit takes none, and are still frequent values. Seen failing first in `test_numbers_that_arent_finite_…` and `test_dates_that_arent_finite_…`.
- **DuckDB's `try_strptime` reads `infinity` and `-infinity` as 1900-01-01** whatever the format, a date the file never wrote. A cast that sees an infinite date now goes first.
- **A profile could start while values were being worked out,** the two DuckDB jobs the plan refuses the other way round. Starting a profile is now refused with the same message. Seen failing first in `test_a_profile_wont_start_while_values_are_being_worked_out`.
- **Closing the values or declining the confirmation dropped the focus** to the page, so a keyboard user started again from the top. It now goes back to the column's "Show values" button. Seen failing first in two Vitest tests.
- Histogram rows are keyed by position, as edges printed to 15 digits can read the same.
- **Known limits:**
  - Asking for a flagged column's values before the findings have loaded gets the server's refusal as a message, not the question. Asking again once they've loaded asks it.
  - A run made before T09 has no possibly-sensitive findings, so it asks for no confirmation. No release has such runs.
  - Reading every row after a fast profile, not the first 100,001, would give the same values, only slower, so no test catches it.

## Verification
Verified 2026-11-07 on the uncommitted branch `feat/cfx-app-csv-profile-t10-values-on-request`, against spec revision 4.

| Criterion | Evidence | Result |
|---|---|---|
| AC-29 | `test_a_possibly_sensitive_columns_values_need_the_extra_confirmation`, `test_a_confirmation_not_given_as_true_is_no_confirmation` and `test_a_column_not_flagged_needs_no_confirmation` through real runs; the Vitest tests for the question, declining it and the focus after; and the Playwright test showing a flagged email column's values only after confirming, all passing. | PASS |
| AC-30 | The 13 tests in `test_values_on_request.py` (range in the column's own order, the 10 most frequent with ties in order, histograms, statistics by type, day-first dates, values that aren't finite, first rows or every row), the API tests labelling the part of the file and the time, the Vitest tests for the section, and the Playwright test for a number column's histogram, all passing. | PASS |
| AC-31 | `test_values_are_cut_at_1000_characters_and_never_kept_by_the_browser` (`Cache-Control: no-store`), `test_a_number_columns_histogram_and_statistics_are_cut_too`, and the Vitest test showing a cut value marked, all passing. | PASS |
| AC-32 | The tests for a file that has gone, can't be read, or whose columns changed, each giving its message while the saved metrics still show, and the matching Vitest test, all passing. | PASS |
| AC-34 | The GET snapshot in `test_session.py` now covers the values route, `test_the_values_route_keeps_the_session_host_and_origin_checks` (AC-08, AC-09), and `test_values_spill_only_inside_the_workspace_and_stay_within_the_memory_limit`, all passing. Axe found no violations with the question open or values shown. | PASS |

Performance, measured 2026-11-07 on a 1.0 GB, 26,120,000-row file: 0.19 to 0.22 s after a fast profile and 10.51 to 13.21 s after a full one, against 5 s and 60 s. The page cache was probably warm.

Checks run after the review fixes: `uv run --frozen pytest tests/integration tests/golden` (2214 passed, 6 skipped by design), `uv run --frozen pytest tests/e2e` (64 passed), `pnpm --dir apps/web test` (154 passed), `pnpm --dir apps/web build` (type-check and build, exit 0), `uvx ruff check packages apps tests testdata` (all passed), `git diff --check` (clean), the README's secret scan over tracked and new files (clean), `ai-skills knowledge discover` and `ai-skills knowledge check`. Each guard was removed in turn to see a test fail; all were caught except the one noted above.

Not verified: CI, as there's no remote yet, and a cold page cache for the 1 GB timing.
