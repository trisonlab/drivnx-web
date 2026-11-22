---
id: DRIVN-56
legacy_id: CFX-APP-postgres-T11
title: "Values on request"
feature: DRIVN-45
feature_revision: 3
stories: [US-05]
criteria: [AC-28, AC-27]
blocked_by: [DRIVN-53, DRIVN-55]
state: ready
external_key: DRIVN-56
status: CONFIRMED
---

# Values on request

Plane: [DRIVN-56](https://app.plane.so/drivnx/browse/DRIVN-56/) · `CFX-APP-postgres-T11`

## Outcome
A user asks for a column's values and gets them from the database with the run's method and seed, labelled as the same rows only if the object hasn't changed, and a clear message when the database can't be reached.

## Acceptance checklist
- [ ] AC-28 (US-05): Given a column in a completed run, when the user asks for its values, then they're computed by querying the database with that run's method and seed, labelled with that scope, the time computed, and that they're the same rows only if the object hasn't changed since the run (for a view, that the rows may differ), cut at 1,000 characters, and sent with `Cache-Control: no-store`. Given the database unreachable, then a clear message shows, and the saved metrics still show.
- [ ] AC-27 (US-05), its confirmation half, from DRIVN-55's decision 2: a possibly-sensitive column's values "need the extra confirmation" for a database too. DRIVN-55 saves the flag, which `flagged()` in `profile_runs_api.py` checks by field ID; this ticket proves the values route asks for it before querying the database.

## Expected evidence
API tests for each scope label, a changed table, a view, `no-store`, values cut at 1,000 characters, and the server stopped.

## Blocked by
DRIVN-53, DRIVN-55

## Plan agreed with the user
Approved in chat, 2026-11-22. It builds on DRIVN-57's `out_of_date` and `check_connection` (merged in #30); both
tickets edit `profile_runs_api.py` and `ProfileTab.tsx`.

1. **Full-profile values (A):** every row is re-read, bounded by the run's per-object time limit. The read stops the
   way a profile does (`_stop_at_time_limit`/`_stop_read`: interrupt, then a server cancel after 1 s) and is refused
   with "took longer than this run's time limit of N minutes".
2. **Changed objects (B):** an object that is `out_of_date` since the run is refused with 409 "<object> changed since
   this run. Profile it again to see its values." The run's unchanged objects still answer.
3. **Reading:** fast values are re-read with the run's saved method, `percent` and `seed` through `load_object_rows`;
   full values through `load_whole_object`. Both go to `values_for(..., formats={})` under `_values_lock`. Nothing is
   saved.
4. **Route:** `GET /api/profile-runs/{id}/values?schema=&object=&column=[&confirmed=true]`. `ObjectReadResponse`
   gains `schema` and `name`, so an object isn't found by its ambiguous `schema.name`. `ValuesResponse` gains
   `object`, `method` and `rows_read`.
5. **Refusals:** the run isn't completed; busy; the object was stopped at its time limit; the object is no longer
   chosen (AC-11); out of date; a flagged column without confirmation (AC-27's confirmation half). A connection
   failure goes through `read_failed`, and the UI shows a 422's message.
6. **Label:** the run's reading, plus "the same rows only if <object> hasn't changed since this run", or for a view
   "the view may return other rows than it did for this run".

Steps:
1. `object_values_of`.
2. The route.
3. UI: Show values in `DatabaseResults`.
4. e2e, with a flagged column's confirmation and the accessibility check.
5. Measure on the 10M-row table.

## Measured (step 5)
On 2026-11-22, on `feat/drivn-56-values-on-request` (uncommitted, on `8ab0a48`), against the fixture: PostgreSQL 17.6
in Docker on the same Linux machine (8 threads, Intel Core i7-1165G7, 15 GB), DuckDB 2.0.0. The table is spike 2's
`spike.big`, 10,000,000 rows and 5 columns, 790 MB (ADR 0005), read as the fixture's superuser, as the spike did:
the fixture's role can't read it. Through `object_values_of`, as the route calls it, three times each, with a script
kept outside the repository:

| Values of | Read as | Rows read | Seconds |
|---|---|---|---|
| `city` (text) | page sample at 100,000 ÷ the estimate, a seed | 104,095 | 0.22, 0.22, 0.20 |
| `amount` (number, with a histogram) | the same | 104,095 | 0.21, 0.21, 0.23 |
| `city` | full, every row | 10,000,000 | 5.47, 5.45, 5.55 |
| `amount` | full, every row | 10,000,000 | 6.21, 5.99, 6.13 |

The process peaked at 896 MB, under the 4 GB DuckDB limit. Of a full read, the server read took 4.90–5.03 s of all
5 columns and 4.63 s of the first 4; working out the values took 0.54 s for text and 1.11–1.14 s for a number.

Against the targets (INFERRED: DRIVN-45 sets none for values on request): fast values take about a fiftieth of the
10 s a fast profile of this table may take, and full values are bounded by the run's time limit (10 minutes by
default), as DRIVN-45 bounds a full profile. Nothing was changed. A full read reads every column, as a full profile
does, because `load_whole_object` reads the server's columns by position, from the first. Reading only the asked
column would save part of the 5 s; it's not needed to meet a target.

## Review (2026-11-22, by the independent reviewer, on `6804cfa`)
Verdict: ready after fixes. Each finding was checked against the code before acting on it.
1. Blank strings were dropped from a database's values, against its run's metrics (risk): fixed in `9f78e65`.
2. A full values read wasn't stopped when DrivnX stopped (it waited up to the time limit) or was killed (the read went on
   on the server) (risk): fixed in `1060a2a`. Stopping the server's wait for requests needed the read interrupted as
   shutdown starts, before `stop_jobs()`; ADR 0009 Decision 7 says so.
3. A DuckDB error while working out values was a 500 (risk): a 422 in DrivnX's words in `d39129c`.
4. The confirmation test couldn't fail if the route read before refusing (nit): it counts reads in `9bf5a4f`.
5. Pydantic warns that `ObjectReadResponse.schema` shadows `BaseModel.schema` (nit): kept, as nothing calls it.
6. The busy message for a database still says "reading a file" (nit): kept, as it was for a run's start.

## Verification (2026-11-22, on `1060a2a`, after the review's fixes)
| Criterion | Evidence | Result |
|---|---|---|
| AC-28: computed by querying the database with the run's method and seed | `test_postgres_values.py` (9 passed): a page sample equals the server's `TABLESAMPLE SYSTEM … REPEATABLE` at the run's percent and seed, and repeats; a view's first rows; a table larger than its estimate as its first rows; a full run's every row. `test_postgres_values_api.py::test_a_page_sampled_objects_values_are_from_the_pages_its_runs_seed_picks`, with the run's saved seed and percent; fails with the seed replaced by 0 | PASS |
| AC-28: labelled with that scope and the time computed | `test_postgres_values_api.py`: `scope`, `method`, `rows_read`, `computed_at` for whole, page sample, full and view; `Postgres.test.tsx`: "Computed … from about 98,340 rows, sampled by page, and not saved."; `tests/e2e/test_postgres_values.py` in the browser | PASS |
| AC-28: the same rows only if the object hasn't changed (for a view, that the rows may differ) | `Postgres.test.tsx`: the same-rows sentence, and the view's; e2e: the sentence for a table; a changed object refused with 409 (`…changed_since_the_run_are_refused_and_the_others_still_answer`) | PASS |
| AC-28: cut at 1,000 characters, sent with `Cache-Control: no-store` | `test_postgres_values_api.py::test_values_are_cut_at_1000_characters_and_never_kept_by_the_browser` | PASS |
| AC-28: database unreachable, a clear message, the saved metrics still show | API: a password the server refuses gives 422 with its reason and the run's metrics unchanged (`…cant_be_read_say_why_and_the_saved_metrics_still_show`); the function's `ConnectFailure` passes through; UI: a 422's message shows and the row's metrics stay | PASS |
| AC-28: the server stopped (the ticket's expected evidence) | No test with the server stopped or not listening: the refusal tested is the password's. `read_failed` handles every `ConnectFailure` alike (INFERRED) | NOT RUN |
| AC-27, the confirmation half: a possibly-sensitive column's values need the extra confirmation | API: 409 without `confirmed=true`, values with it, before the database is read; UI: the dialog, nothing requested until confirmed, focus back on decline, a column of the same name in another object doesn't ask; e2e with the accessibility check | PASS |
| AC-33 for values: a stop or a kill during values leaves nothing on the server | `test_postgres_values_stopping.py` (3 passed): DrivnX stopped mid full values exits within 5 s; killed mid fast or full values, nothing is left on the server within 5 s; each seen failing before `1060a2a` | PASS |
| Blank strings are values, as the run counted them | `test_postgres_values.py::test_a_database_columns_blank_strings_are_values_as_its_run_counted_them`, seen failing before `9f78e65` | PASS |
| Decision A: full values bounded by the run's time limit | `test_postgres_values.py`: stopped at the limit with nothing left on the server, and a read longer than other statements within it; API: 422 "Reading … took longer than this run's time limit of 1 minute." | PASS |
| Refusals in the plan | API tests: not completed, no such object or column, stopped at the time limit, no longer chosen (AC-11), out of date, flagged, busy, no password after a restart | PASS |
| Step 5: measured on the 10M-row table | "Measured (step 5)" above | PASS |

Checks run, all on the working tree above:
- `uv run --frozen pytest tests/integration tests/golden`: 2621 passed, 8 skipped (as before this ticket), exit 0.
- `uv run --frozen pytest tests/e2e`: 85 passed, exit 0.
- `pnpm --dir apps/web test`: 263 passed. `tsc --noEmit`: exit 0. `vite build`: built. Run on `6804cfa`; nothing in
  `apps/web` changed since.
- CI's scans: detect-secrets over every tracked file on `6804cfa` and over the files changed since, exit 0; pip-audit
  and `pnpm audit`: no known vulnerabilities, with no dependency changed since.
- `.ai/hooks/run.py pre-commit`: exit 0, with nothing staged. `ai-skills knowledge check`: the graph is out of date with this change, and two facts are stale from before it.

Not verified: a stopped server, as above; CI on GitHub, as nothing is pushed.
