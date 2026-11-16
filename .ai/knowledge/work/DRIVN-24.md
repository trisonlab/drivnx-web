---
id: DRIVN-24
legacy_id: CFX-APP-csv-profile-T04
title: "Fast profile, end to end"
feature: CFX-APP-csv-profile
feature_revision: 5
stories: [US-02, US-03]
criteria: [AC-07, AC-09, AC-13, AC-15, AC-16, AC-17]
blocked_by: [DRIVN-21, DRIVN-22]
state: done
external_key: DRIVN-24
status: CONFIRMED
---

# Fast profile, end to end

Plane: [DRIVN-24](https://app.plane.so/drivnx/browse/DRIVN-24/) · was `CFX-APP-csv-profile-T04`

## Outcome
The thin path through every layer: a user presses Fast profile, a background run reads the first rows, and the Profile tab shows the first metrics with their labels while the header shows the run's status.

## Acceptance checklist
- [x] AC-07 (US-02): Given a connected CSV and no run in progress, when the user chooses Fast profile, then a run reads at most the first 100,000 rows and completes with its results shown.
- [x] AC-09 (US-02): Given a run in progress, then the header's profile status and the Profile tab show that it's running, and a second run is refused with a clear message.
- [x] AC-13 (US-02): Given a profile job, then it creates nothing in the current directory, and any spill files are inside `<workspace>/duckdb-tmp/`.
- [x] AC-15 (US-03): Given any metric, then the screen shows its scope (whole file or first rows), its precision (exact or approximate) and its run's time and ID, and results from different scopes are never mixed without a label.
- [x] AC-16 (US-03): Given a fast profile of a file with more than 100,000 rows, then its metrics are labelled "first 100,000 rows, not random", and no total row count is claimed.
- [x] AC-17 (US-03): Given a fast profile of a file with at most 100,000 rows, then its metrics are labelled as the whole file.

## Expected evidence
Tests naming each criterion, and a run on files above and below 100,000 rows.

## From planning
- `packages/profiling` has to be added to the wheel's package list in `pyproject.toml`.
- The first slice's AC-33 checks in `App.test.tsx` and `tests/e2e/test_first_launch.py` change for the status and the Profile tab. The other tabs stay marked as not available.
- T01 measured with DuckDB limited to 4 GB, and peak memory reached about 3 GB. DuckDB's default is 80% of the machine's memory, so profile jobs should set a limit rather than risk slowing the user's other programs.

## Plan agreed with the user
- **The run:** the route starts a run and returns. A daemon thread loads the first rows into a DuckDB temp table, measures them, saves observations and ends the run. The browser polls once a second, only while a run is `running`.
- **Metrics here:** `rows_read` and `missing_count`. T05 adds the rest.
- **First rows or whole file:** load 100,001 rows and measure only the first 100,000, so a file of exactly 100,000 rows counts as the whole file. The extra row is never counted.
- **Layers:** `connectors.load_rows` reads the same ways as the preview, `profiling.measure_first_rows` knows nothing about CSV, and `local_api/profile_jobs.py` joins them to `artifacts`.
- **One run at a time:** `artifacts.start_only_profile_run` checks for a `running` run inside the write transaction that starts one, and raises `RunInProgress`. `start_profile_run` stays as it is, as existing tests rely on it.
- **Routes:** `POST /api/profile-runs` (`{source_id, mode: "fast"}`; 202, 404, 409, and 422 for `full` until T06), `GET /api/profile-runs` (newest first, with each run's source and whether its dataset is still current) and `GET /api/profile-runs/{id}` (metrics only once `completed`).
- **Run values:** `mode="fast"`, `sample_size=100000`, `strategy="first_rows"`. Observation scope is `sample` or `full`, and precision is `exact`.
- **The job:** sets `memory_limit = '4GB'`, saves at most 1,000 observations per transaction, and ends as `completed` or `failed`. If the file's columns no longer match the saved ones, it fails and saves no metrics. It never logs or saves an exception's text, as DuckDB's errors quote rows.
- **The screen:** Profile is always selected, with its panel above the source details and preview, and the other tabs stay marked as not available. The Fast profile button stays enabled, so a refused run shows its message. Only a run on the current dataset is shown, so a correction can't leave old results on screen unlabelled before T11.
- **Busy workspace:** a `WorkspaceBusy` handler in `app.py` returns 503 with the "DrivnX is busy" message. The existing screens' display of it doesn't change.
- **Build order:** read and measure, then store and run, then the screen, then the whole journey against the installed wheel and one manual run on a 1 GB file.
- **To prove first (not yet shown):** DuckDB accepts parameters in `CREATE TEMP TABLE … AS`, and spills a temp table to `duckdb-tmp`.
- **Accepted limits:** if saving a run's final status itself fails, the run stays `running` and blocks new runs until a restart marks it `interrupted`. A file with a chosen skip that the preview reads through its path for uneven lines ends as `failed`, which T05 should fix.

## What building it showed
- **Both points to prove hold.** DuckDB accepts parameters in `CREATE TEMP TABLE … AS`. A temp table spills to `duckdb-tmp`: seen at a 100 MB limit, while at 30 MB DuckDB can't run the read at all.
- **Saved columns are matched as the first columns, not all of them.** When connecting, `extra_field_in_a_row.csv` saves 3 columns, but reading it with padding gives 4, as the preview shows. The saved columns are measured, and the surplus one gets no metric as it has no field to point at.
- **A run's ending is in `finally`, and only expected errors are caught.** Expected failures drop their text, as DuckDB's can quote a row. A mistake in DrivnX still ends the run as `failed`, and its traceback stays visible.
- **Indicative only:** on a 1 GB, 26,120,000-row, 5-column UTF-8 file, two runs each took 0.15 s from starting to ending, with a peak of 232 MB. This was on a RAM disk with only two metrics, so it says nothing about the 5 s target. T12 measures that.
- **A real browser found what mocks couldn't.** A refusal stayed on screen after the run ended, and a tab that was refused never learned of the run. Both are fixed and held by tests. The same one-off probe, with a slow 400-column file and two tabs, now shows them right.
- **Known limits of those fixes:**
  - A tab left open and untouched says "Not run yet" until it reads the runs again, so a run started in another tab doesn't show there straight away.
  - If a run ends in the moment between a refusal and reading the runs again, the refusal stays until the next click.

## Review
An independent review and the author's own checks found these, all fixed and held by tests unless noted.
- **A column called `rowid` stopped every run.** Columns are now used by position, through aliases in the query, so a name from the file never reaches the SQL. The review's suggested `LIMIT` subquery was measured and rejected: at a 100 MB limit it ran out of memory where this doesn't, and it was about 7 times slower.
- **With no header, a wider row renumbers the generated names** once there are more than 10 columns, so only a file with a header has its names checked. Too few columns still fails, because DuckDB refuses the aliases.
- **Only `CsvError`, `duckdb.Error`, `OSError` and `WorkspaceBusy` are caught.** A `ValueError` or SQLAlchemy error is a mistake in DrivnX, so it ends the run as `failed` and stays visible. Neither holds row text: SQLAlchemy hides parameters here.
- **The tab says when a run is on another file,** as AC-09 asks of both the header and the tab.
- **Decided by the user:** a row wider than the table inside the first rows fails the run rather than being cut or skipped, as in the preview, because leaving it out would make the counts and "whole file" quietly wrong. A row just after 100,000 can do the same, as DuckDB parses in chunks. T07 gives this a clear message.
- **Accepted limit:** a column named `rowid` after the saved ones fails the run. T07 refuses that kind of changed file. T05 removed this limit, as `load_rows` now names every column by position.
