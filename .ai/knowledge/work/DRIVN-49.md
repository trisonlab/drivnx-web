---
id: DRIVN-49
legacy_id: CFX-APP-postgres-T04
title: "Discover and choose objects"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-02]
criteria: [AC-08, AC-09, AC-10, AC-11, AC-13]
blocked_by: [DRIVN-47]
state: done
external_key: DRIVN-49
status: CONFIRMED
---

# Discover and choose objects

Plane: [DRIVN-49](https://app.plane.so/drivnx/browse/DRIVN-49/) · `CFX-APP-postgres-T04`

## Outcome
A user sees every schema they can see with its tables, partitioned tables, views and materialized views, opens one to see its columns and declared keys, and chooses what to profile. Objects without read access are shown but can't be chosen, and a refresh shows the database as it is now.

## Acceptance checklist
- [x] AC-08 (US-02): Given a connected source, then DrivnX lists every schema the user can see, except the system and temporary schemas, with its tables, partitioned tables (partitions hidden), views and materialized views, each with its kind and estimated row count labelled as an estimate. Foreign tables aren't listed.
- [x] AC-09 (US-02): Given a listed object, when the user opens it, then its columns are shown with their PostgreSQL type, normalised type and whether they allow nulls, and its declared primary key, unique constraints and foreign keys (with the table and columns referenced), without reading any row.
- [x] AC-10 (US-02): Given an object the user can't `SELECT` from, then it's listed as "no read access" and can't be chosen.
- [x] AC-11 (US-02, this ticket's half: choosing, saving, kept after a restart; the preview and profile limit is DRIVN-50's and DRIVN-51's): Given the list, when the user chooses whole schemas or single objects and saves, then the selection stays after a restart, and only chosen objects can be previewed or profiled.
- [x] AC-13 (US-02, this ticket's half: the refresh; refusing a run is DRIVN-51's): Given an object dropped, or whose columns or types changed since discovery, when the user refreshes the source, then the list and the columns show the database as it is now. When a run starts on such an object, it is refused with a message pointing to the refresh.

## Expected evidence
Integration tests against the fixture for each kind of object, foreign tables left out, declared keys, no read access, the selection kept after a restart, and a refresh after a drop and a column change. Discovery timed on 1,000 objects.

## Notes
AC-13 is shared with DRIVN-51: the refresh is here, refusing a run on a changed object is there.

## Plan agreed with the user
Approved in chat, 2026-11-14, with two decisions:
- **A materialized view never refreshed** (`WITH NO DATA`) is listed as "Not populated" and can't be chosen: the
  role may `SELECT` it, but every read fails with "has not been populated".
- **Choosing a whole schema** ticks that schema's readable objects as they are now. It isn't a saved rule: an object
  that a later refresh finds isn't chosen until the user chooses it.

The backend was built first by the build loop, uncommitted, and taken over here: discovery from PostgreSQL's
catalog in one snapshot with a 30 s `statement_timeout`; migration `0008` making each object a dataset and its
columns fields, with `declared_key`, `database_schema` and a `chosen` flag only a readable object can have; a refresh
replacing an object whose columns changed, as a CSV correction does; and `POST /api/sources/{id}/discover`,
`GET /api/sources/{id}/objects`, `PUT /api/sources/{id}/selection` and `GET /api/sources/{id}/objects/{object_id}`.
`uv run --frozen pytest tests/integration -k "postgres or session"` with `DRIVNX_REQUIRE_POSTGRES=1`: 243 passed.

What the planning probe showed, against the fixture (PostgreSQL 18):
- The catalog lists 1,013 objects in 15–29 ms, their 3,042 columns in 23–33 ms and 2,007 keys in about 50 ms, and no
  user table is scanned. DuckDB's own catalog can't list them: it includes partitions, foreign tables and system
  tables, and shows views as tables.
- `postgres_query` wraps the query and selects its columns by name, so each needs its own alias.
- DuckDB reads `numeric` without a precision as `DOUBLE`, json as `VARCHAR` and a domain over `integer` as `VARCHAR`,
  so the normalised type comes from PostgreSQL's base type.
- A partitioned table's own `reltuples` stays `-1` when only its partitions are analysed, which is what autovacuum
  does: partitions at 3,660 and 3,340, the parent at -1 until `ANALYZE` on it.
- A foreign key to a partitioned table has a copy for each partition (`conparentid <> 0`).
- `SET statement_timeout` through `postgres_execute` doesn't hold; in the connection's `options` it does, and stopped
  a 31 s query.

Steps:
1. **A partitioned table's estimate** is the sum of its partitions' (`pg_partition_tree`), leaving out those never
   analysed, and none only when no partition has one (`connectors/postgres.py`). Verify: partitions analysed, the
   parent not, gives their sum.
2. **Not populated**: `dataset.populated` in `0008`; such a view can't be chosen. Verify: discovery, storage
   (`chosen` refused), selection API (422), and a refresh after `REFRESH MATERIALIZED VIEW` makes it choosable.
3. **Client** (`apps/web/src/api.ts`): `getObjects`, `refreshObjects`, `saveSelection`, `getObject`, with the types
   `DatabaseObject`, `ObjectsList` and `ObjectDetail`.
4. **The list** (`DatabaseObjects.tsx`, under `PostgresDetails` in the centre): schemas with their objects, kind and
   "about N rows (estimate)" or "no estimate"; "No read access" and "Not populated" not choosable; a schema's
   checkbox and single objects; Save selection; Refresh with the time of the last listing and its failure; after a
   restart the saved list, with Refresh asking for the password first; "not listed yet" with a button to list.
   Verify in Vitest, one behaviour at a time.
5. **An object** (`ObjectDetails.tsx`, opened from its name): each column's name, PostgreSQL type, normalised type and
   whether it allows nulls (a view's columns say PostgreSQL doesn't record it), and its primary key, unique
   constraints and foreign keys with what they reference. Verify in Vitest.
6. **e2e** (`tests/e2e/test_choose_postgres_objects.py`) against the installed wheel and the fixture, with objects made
   by `postgres_admin`: listed with an estimate, opened, no read access, chosen and saved, kept after a restart, a
   column added and refreshed; axe on each new state.
7. This ticket, `ai-skills knowledge check`, and the Plane item.

AC-11's "only chosen objects can be previewed or profiled": every read of a database still answers 409 here; the
first routes that read an object, DRIVN-50 and DRIVN-51, prove the limit.

Out of scope: preview (DRIVN-50), profiling and refusing a run on a changed object (DRIVN-51), unique indexes that
aren't constraints, row-level security, and a schema chosen as a rule.

## What building it showed
- **Step 1:** with only the partitions analysed, the test saw `None` for the partitioned table (red), then 5: the 2024
  and 2025 partitions' rows, leaving out a sub-partition never analysed. A partitioned table none of whose partitions
  has an estimate still has none.
- **Step 2:** `populated` is in the connector, `dataset.populated` in `0008` (unmerged, so changed in place), the
  `chosen` check, a refresh (choosable after `REFRESH MATERIALIZED VIEW`, unchosen after `... WITH NO DATA`), and the
  API. The new `CHECK` made the hand-written object rows in the store tests need `populated`, so each refusal test
  still fails only for its own reason. `ObjectFound.populated` defaults to true; the API passing it on was checked by
  mutation (removed, the API test fails). Each rule broke a test with it removed: the selection's filter, the
  refresh's update, unchoosing, and the database's `CHECK`.
- **Changed from the plan:** after a restart the list shows once the password is given, not before. DRIVN-47's
  AC-05 test forbids any request about the source before the password, and this keeps it as it was; the screen then
  needs no "refresh asks for the password" state. DRIVN-47's "nothing asks for its rows" test was narrowed from any
  request naming the source to rows, settings and a run, as its name says; the list is read from the workspace.
- **Steps 3 to 5:** `DatabaseObjects.tsx` (the list, choosing, saving, Refresh) under `PostgresDetails`, and
  `ObjectDetails.tsx` (columns and keys) opened from an object's name. A database not listed yet showed an empty
  selection with a Save button (seen failing), and now shows only the button to list it. A refresh closes an opened
  object and starts again from the saved selection, since ids can change; that, and "Selection saved." clearing on a
  change, were written ahead of their tests, so each was checked by mutation. Vitest: 200 passed; `tsc` and
  `pnpm build` passed.
- **Step 6:** `tests/e2e/test_choose_postgres_objects.py`: listed with an estimate, no read access, an object's
  columns and foreign key, a schema chosen and saved, kept after a restart, and a new column after a refresh, with axe
  on each state. Opening an object while a refresh was pending lost it when the refresh closed it, so the test waits
  for the listing time to change. With a refresh that never stores changed columns, the e2e fails.
- **Step 7:** the knowledge index was rebuilt (`ai-skills knowledge discover`). The Plane item was already In
  Progress, moved there by the build loop.

## Verification
2026-11-15, on the working tree of `feat/drivn-49-discover-objects` (HEAD `c566a64` and the same diff before and
after the runs), with the fixture serving TLS from PostgreSQL 18's binaries (`PGBIN`). No PostgreSQL test skipped:
the only skips are the six by-design ones in `test_fast_profile.py`.

| Criterion | Evidence | Result |
|---|---|---|
| AC-08: every schema but the system and temporary ones, with tables, partitioned tables (partitions hidden), views and materialized views, each with its kind and an estimate labelled as one; no foreign tables | `test_postgres_discovery.py`: every kind with its estimate, partitions and foreign tables not listed, system and temporary schemas not listed, a partitioned table's estimate from its analysed partitions; `test_postgres_objects_api.py`: listed on saving, every kind on refresh, 1,000 objects listed in 0.511 s (target 5 s); Vitest: "each schema is listed with its objects, their kinds and estimated rows labelled as estimates", "a database not listed yet…"; e2e `test_a_saved_database_lists_its_tables_and_opens_one` | PASS |
| AC-09: an object's columns with PostgreSQL type, normalised type and nulls, and its primary key, unique constraints and foreign keys with what they reference, reading no row | `test_postgres_discovery.py`: types, normalised types and nulls, a view's columns, declared keys, odd key names, a foreign key to a partitioned table once, `test_discovery_reads_no_row`; `test_postgres_objects_api.py`: opening an object, one opened in 0.005 s (target 2 s); Vitest: columns and keys with only the workspace read, a view's nulls "Not recorded"; e2e: the foreign key and a column row | PASS |
| AC-10: an object the user can't `SELECT` from is listed as "no read access" and can't be chosen | `test_postgres_discovery.py`: no `SELECT` or no schema `USAGE` is not readable, a materialized view never refreshed is not populated; store: the database's `CHECK`s and `NotChoosable`; API: refused whole with 422 and nothing changed; Vitest and e2e: listed with the reason and a disabled checkbox | PASS |
| AC-11: choosing whole schemas or single objects and saving; kept after a restart; only chosen objects previewed or profiled | Store: exactly the chosen saved, kept after reopening, refused whole; API: `test_choosing_objects_saves_exactly_them`, `test_a_saved_selection_stays_after_a_restart` with real processes; Vitest: a whole schema is its choosable objects, saving sends exactly them; e2e: a schema chosen, kept after a restart. Every preview and profile route still answers a database with 409 (`test_what_only_a_file_can_do_is_refused_for_a_database_with_a_reason`); that only chosen objects can be is proved when those routes read an object | PASS for choosing and keeping; the preview and profile limit is DRIVN-50's and DRIVN-51's |
| AC-13: after a drop or a column or type change, a refresh shows the database as it is now | Store: a drop keeps the dataset for earlier runs, a column change gives a new object and keeps it chosen, an unchanged object keeps its id; API: `test_refreshing_after_a_drop_and_a_column_change_shows_the_database_as_it_is_now`, a failed refresh keeps the list; Vitest: refresh, its failure, the opened object closed; e2e: a new column after a refresh | PASS for the refresh; refusing a run on a changed object is DRIVN-51's |

Checks run:
- `DRIVNX_REQUIRE_POSTGRES=1 uv run --frozen pytest tests/integration tests/golden`: 2410 passed, 6 skipped (all in
  `test_fast_profile.py`, by design). `… pytest tests/e2e`: 78 passed. `pnpm --dir apps/web test`: 200 passed.
  `pnpm build` (`tsc --noEmit && vite build`): passed.
- `.ai/hooks` pre-commit on every changed and new file: passed. `ai-skills knowledge check`: consistent with
  evidence.
- The diff read for leftovers: the one `print` is the timing test's measurement, recorded above.

Not run: CI on macOS and Windows, until a push. No Python linter is installed here (no ruff or mypy in `.venv`), so
none was run.

## Review, round 1
2026-11-15, by the independent reviewer: ready after two fixes, both made test-first (each test seen failing first).
- **AC-09:** a materialized view's columns said "Allows nulls: Yes"; PostgreSQL never records NOT NULL for them.
  They now say "Not recorded", as a view's do (`ObjectDetails.tsx`; Vitest "a materialized view's columns say
  PostgreSQL doesn't record whether they allow nulls").
- **A list the workspace can't load** showed only the heading; it now says so in an alert (`DatabaseObjects.tsx`;
  Vitest "a list the workspace can't load says so").
- Left as nits: two refreshes overlapping can save the older catalog last (one tab's button is disabled while one
  is pending); a domain over a domain is normalised from its immediate base only, as the query's comment says; a
  failed save or refresh falls back to the connection test's message. ADR 0007 not listing `statement_timeout` was
  fixed after the architecture diff: Decision 2 now names it and says a profile sets its own limit.
- For DRIVN-51: every connection now has `statement_timeout=30s`, so profiling will need its own per-table limit.

Verified again after the fixes, same HEAD, the fixture from PostgreSQL 18's binaries (`PGBIN`):
`DRIVNX_REQUIRE_POSTGRES=1 uv run --frozen pytest tests/integration tests/golden`: 2410 passed, 6 skipped (by design,
`test_fast_profile.py`); `… tests/e2e`: 78 passed (on the UI bundle from before the fixes; after `pnpm build`,
`test_choose_postgres_objects.py` and `test_connect_postgres.py`: 6 passed); `pnpm --dir apps/web test`: 202 passed;
`pnpm build`: passed; `.ai/hooks` pre-commit on every changed and new file: passed; `ai-skills knowledge discover`,
then `knowledge check`: consistent with evidence.

## Blocked by
DRIVN-47, done (PR #19).
