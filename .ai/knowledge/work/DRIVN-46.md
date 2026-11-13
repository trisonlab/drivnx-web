---
id: DRIVN-46
legacy_id: CFX-APP-postgres-T01
title: "Fixture and bundled extension"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-01, US-07]
criteria: [AC-07, AC-34]
blocked_by: []
state: ready
external_key: DRIVN-46
status: CONFIRMED
---

# Fixture and bundled extension

Plane: [DRIVN-46](https://app.plane.so/drivnx/browse/DRIVN-46/) · `CFX-APP-postgres-T01`

## Outcome
The installed wheel attaches a PostgreSQL database read-only, loading DuckDB's PostgreSQL extension from inside the package with its signature checked and no download, against a Docker Compose fixture built from synthetic data. The spike's measurement runs against the fixture. This settles how CI runs PostgreSQL on macOS and Windows, where GitHub's runners can't run the Linux image.

## Acceptance checklist
- [ ] AC-07 (US-01): Given the installed wheel on each tested platform, when DrivnX connects, then it loads the PostgreSQL extension from inside the package with DuckDB's signature check on, and DuckDB makes no request to any host other than the database server. Given another platform, then the screen says PostgreSQL isn't available on it.
- [ ] AC-34 (US-07): Given `testdata/` and the fixture, then the fixture is a Docker Compose service with a pinned image, its database is built by a committed script from synthetic data only, and tests that need it say so when it isn't running.

## Expected evidence
The installed wheel attaching the fixture with downloads off, on each tested platform, and the platform message elsewhere. The fixture's script and pinned image. `tests/measure_postgres_sample.py` run against the fixture. CI on Ubuntu, macOS and Windows, or NOT RUN with the reason where PostgreSQL can't run.

## Notes
AC-07 is shared with DRIVN-47: this ticket proves the extension loads from the package; DRIVN-47 proves it when a user connects.

## Plan agreed with the user
Approved in chat, 2026-11-13, with two decisions:
- **CI gets PostgreSQL from one fixture script,** `tests/postgres_fixture.py`, used locally too: the Compose fixture where Docker works, the Windows runner's own PostgreSQL 17, and Homebrew's postgresql@17 on macOS, each loading the same SQL. `test_ci.py` forbids platform checks in the workflow, so they live in that script alone. CI sets `DRIVNX_REQUIRE_POSTGRES=1`, so a test fails rather than skips without a server.
- **One wheel carries all three platforms' extension** (about 55 MB to download, 170 MB installed), keeping one build command for a release. Wheels per platform were rejected.

Steps:
1. **Fetch and verify at build time** (`hatch_build.py`, `pyproject.toml`, `.gitignore`). A manifest pins the DuckDB version and each platform's sha256. `fetch_postgres_extension(platforms, folder, base_url)` checks the hash before writing and unpacks to `packages/connectors/postgres_extension/<platform>/`. A wheel gets all three, an editable install this machine's if missing. The build fails when the locked `duckdb` isn't the manifest's. `duckdb==2.0.0`.
2. **Load from the package** (`packages/connectors/postgres.py`): `PostgresUnavailable`, `extension_file(platform)`, `load_postgres(connection)` by path, downloads off, signature check on.
3. **The fixture**: `testdata/postgres/compose.yaml` (PostgreSQL 18 pinned by digest, `127.0.0.1:54329`), hand-written synthetic `testdata/postgres/fixture.sql`, `tests/postgres_fixture.py start|stop`, and a `postgres_fixture` fixture that skips with the command to run, or fails under `DRIVNX_REQUIRE_POSTGRES=1`.
4. **CI**: one step running the fixture script, the same command in the README (first slice AC-53), and a `test_ci.py` rule allowing only this script's platform handling.
5. **Measure on the fixture** with `tests/measure_postgres_sample.py`, and record the figures here.
6. **ADR 0006** for the bundled extension, its pinned hashes and the fixture script; this ticket; the index.

Out of scope: the connect form, API and `source` migration (DRIVN-47), credentials (DRIVN-48), discovery (DRIVN-49), wheels per platform.

## What building it showed
- **DuckDB's repository refuses Python's default User-Agent** with HTTP 403 (curl with the same header got 403, others
  200), so the build sends `User-Agent: build`, which says nothing about the project or the user.
- **Given a name instead of a path, `LOAD` looks in `~/.duckdb/extensions`.** With nothing bundled, the first version
  passed `'None'`, and DuckDB went looking for an extension of that name. `load_postgres` now raises
  `PostgresUnavailable` before DuckDB is asked anything.
- **Two tests could only pass at first, so each was shown failing with what it guards broken:** the signature test with
  `allow_unsigned_extensions` turned on (`DID NOT RAISE`), and the installed-wheel test with the extension left out of the
  wheel (`PostgresUnavailable … linux_amd64`).
- **A required fixture that's missing first showed as a skip, not a failure,** as `pytest.skip` escaped the test that
  expected `pytest.fail`. That's the hole `DRIVNX_REQUIRE_POSTGRES` closes.
- **This machine's Docker has no Compose**, so the script checks Compose itself, not just Docker, before choosing that path.
  The own-server path ran here instead, with PostgreSQL 18.6's binaries through `PGBIN`. The Compose, Homebrew and
  Windows paths haven't run anywhere yet; CI is their first run.
- **Debian's PostgreSQL puts its socket in `/var/run/postgresql`,** which this user can't write, so the own server listens
  on TCP only, set in `postgresql.conf` rather than on `pg_ctl`'s command line, which shells quote differently.
- **The secret-scan hook flagged the fixture's test passwords.** Each of those lines carries the marker each scanner
  documents for a known test value, and both scanners pass. `test_testdata.py` (first slice AC-54) names the two
  hand-written fixture files, as it asks, and still fails on anything else.
- **The wheel is 50.3 MB and 157.7 MB installed** (ADR 0006), against the plan's estimate of about 55 MB and 170 MB.

## Measured
Step 5, 2026-11-13: `tests/measure_postgres_sample.py` against the fixture, started by `tests/postgres_fixture.py`
from this machine's PostgreSQL 18.6 binaries (Compose isn't installed here), with its folder on the NVMe disk. Two runs,
each timing three times unless stated. The machine was busy: the load average reached about 10 on 8 cores, mostly the
measurement's own 7 or 8 server sessions and DuckDB's threads, so the second run's full scans took about twice the first's.
Spike 2's figures (ADR 0005) are on a cluster with `shared_buffers` at 256 MB; the fixture has the default, 128 MB.

| | Fixture, run 1 | Fixture, run 2 | Spike 2 (ADR 0005) | Target (spec) |
|---|---|---|---|---|
| `TABLESAMPLE SYSTEM` sample, load | 0.045–0.097 s | 0.043–0.054 s | 0.034–0.039 s | |
| Profiling that sample | 0.47 s | 0.51 s | 0.38–0.44 s | fast profile ≤ 10 s |
| `SYSTEM` repeats on an unchanged table | yes | yes | yes | |
| `LIMIT 100000` | 100% from the first 30%, not repeatable | the same | the same | |
| Full profile, one thread | 5.7–6.0 s | 14.0–14.5 s | 4.0–4.2 s | measured, bounded by the time limit |
| Cancel: DuckDB returns / nothing left on the server | 0.20 / 0.26 s | 0.26 / 0.38 s | 0.10–0.14 / 0.12–0.17 s | ≤ 5 s |
| Listing 1,004 objects / 3,024 columns | 0.03–0.04 / 0.01–0.04 s | 0.01–0.02 / 0.02 s | 0.003 / 0.009 s | ≤ 5 s / ≤ 2 s |
| `statement_timeout` of 2 s stopped the scan | no (5.6 s) | no (7.6 s) | no | |

Every decision in ADR 0005 holds on the fixture: `SYSTEM` spreads across the table (27.7% from the first 30%, all 7
cities) and repeats, `LIMIT` doesn't, views refuse `TABLESAMPLE`, materialized views and partitioned tables accept it, and
DuckDB refused every write. As in spike 2, 1,000 updated rows changed `SYSTEM`'s sample in one run and not the other.
Times are slower than spike 2's by 1.2 to 3.5 times, which the load and the smaller buffers would explain (INFERRED, not
isolated). Every target is met with a wide margin on both runs.

## From the review
Each finding was reproduced before it was fixed, and each fix has a test seen failing first unless said otherwise.
1. **`start()` took any fixture folder for a running server**: with a leftover folder it printed "running" and exited 0
   on a closed port. A real one existed here, from an `initdb` at 05:19 whose origin wasn't found. `start()` now asks
   whether the port answers, removes a folder with nothing listening and starts again, and a start that fails partway
   stops what it began and removes its folder. Tests: `…left_by_a_start_that_died_is_removed_and_started_again`,
   `…fails_partway_leaves_no_folder_behind`. Checked with the real server: a second `start` leaves it alone, and a
   stale folder is replaced by a server that answers. Not tested: a failure after the server started (`psql` failing).
2. **The download had no timeout**: against a server that accepts and never answers it was still waiting after 20 s.
   It now times out after 60 s. Test: `…repository_that_never_answers_fails_the_fetch_instead_of_hanging`, shown failing
   with the timeout dropped (the fetch was still running after 10 s).
3. **Each test run kept about 0.65 GB more** (11 extension copies, 551 MB, and two wheels), and with pytest keeping three
   runs this machine's 7.7 GB RAM-disk `/tmp` filled: a run failed with `No space left on device`. pytest now keeps
   only failed tests' folders (`tmp_path_retention_policy = "failed"`): after two passing runs they held 12 KB. No test.
4. **The no-PostgreSQL message was wrong on this very machine**, which has Docker without Compose and PostgreSQL off the
   path. It now names Compose, `PGBIN`, Homebrew and `pg_ctl`. Test: `…the_message_says_what_would_work`.
5. **An interrupted write could leave part of the extension**, which a later `uv sync` would trust. It's written under
   a temporary name and renamed. A judgement fix with no test: the existing fetch tests show nothing else changed.
6. **Homebrew updates itself before installing**, slow and sometimes flaky on CI: the install now sets
   `HOMEBREW_NO_AUTO_UPDATE=1`. Unverified until the macOS job runs.

With the user's go-ahead, pytest's earlier temp folders (6.9 GB) and the stale fixture folder were deleted.

## Verification
2026-11-13, on the working tree of `feat/drivn-46-fixture-and-extension`, with the fixture started from PostgreSQL
18.6's binaries (`PGBIN`) and `DRIVNX_REQUIRE_POSTGRES=1`, so no PostgreSQL test could skip.

| Criterion | Evidence | Result |
|---|---|---|
| AC-07: the wheel carries the extension, pinned | `test_postgres_extension_build.py` (4) and `test_wheel.py`'s two extension tests: a hash or DuckDB version that doesn't match stops the build; a real build holds all three platforms; `uv sync` fetches this machine's | PASS |
| AC-07: loaded from the package, signature checked, nothing downloaded | `test_postgres_extension.py`: loads with every way to download made to fail (a control `INSTALL` fails); a one-byte-changed copy is refused, and that test fails with `allow_unsigned_extensions` on; the installed wheel loads its own, and fails when built without it | PASS on Linux |
| AC-07: when DrivnX connects, on each tested platform | `test_the_installed_wheel_reads_a_postgres_database_with_no_way_to_download`: the installed wheel attaches the fixture `READ_ONLY` and reads `fixture.sql`'s rows | PASS on Linux; macOS and Windows NOT RUN until CI |
| AC-07: another platform is told PostgreSQL isn't available | `test_a_machine_with_no_bundled_extension_is_told_so_by_platform` (`PostgresUnavailable`); the screen is DRIVN-47's | PASS (the API part) |
| AC-34: a Compose service with a pinned image | `test_the_compose_fixture_pins_its_image_and_listens_on_loopback_only` | PASS; starting it NOT RUN here (no Compose installed), first run in Ubuntu CI |
| AC-34: built by a committed script from synthetic data | `test_the_fixture_holds_exactly_what_its_sql_built`; `test_testdata.py` names the hand-written files | PASS |
| AC-34: tests say so when it isn't running | The skip and required-failure tests; with the fixture stopped, the 2 PostgreSQL tests skip naming the command | PASS |

Checks run:
- `uv lock --check`: resolved, no change.
- `uv run --frozen pytest tests/integration tests/golden`: 2240 passed, 6 skipped (the existing by-design skips in
  `test_fast_profile.py`, none for PostgreSQL). After the review's fixes, again with the fixture required: 2244 passed,
  6 skipped, and e2e 72 passed, with pytest's temporary folders at 12 KB afterwards.
- `uv run --frozen pytest tests/e2e`: 72 passed. `pnpm --dir apps/web test`: 163 passed.
- CI's secret scan over every tracked and new file: passed, after marking the extension's three public checksums.
  Before that it failed on them and on a sentence in this ticket.
- pip-audit and pnpm audit: no known vulnerabilities. `.ai/hooks` pre-commit on every changed file: passed.
  `ai-skills knowledge check`: consistent.

Not verified: the Compose, Homebrew and Windows fixture paths, and AC-07 on macOS and Windows, until CI runs them.
No workflow linter was run (actionlint isn't installed). The ticket stays open until CI passes on all three systems.

## Blocked by
None - can start now.
