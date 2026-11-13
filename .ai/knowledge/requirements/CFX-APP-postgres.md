---
id: CFX-APP-postgres
external_key: DRIVN-45
kind: feature
title: "Connect a PostgreSQL database read-only, choose its tables and profile them"
summary: "Connect PostgreSQL read-only with the password in the OS credential store, discover schemas and tables, choose what to profile, and get the CSV profile's metrics, findings and values on request for each table."
owner: drivnx-web
lifecycle: approved
revision: 2
approved_revision: 2
approval_evidence: "user approved revision 2 in chat, 2026-11-13"
updated: "2026-11-13"
related: [CFX-APP-first-slice, CFX-APP-csv-profile]
status: CONFIRMED
---

# Connect a PostgreSQL database read-only, choose its tables and profile them

## Problem
DrivnX can profile a CSV, but much of the data its users need to understand sits in a database. M4 adds the first one, PostgreSQL, read-only. A user connects it, sees what's in it, chooses the tables, and gets the same metrics, findings and values on request as for a CSV. This is also the first time DrivnX holds a secret, so the OS credential store that was deferred from the first slice arrives here.

Source of truth: `../drivnx-docs/docs/DrivnX_Phase1_Development_Direction.md` (Sections 2, 5, 7, 10 and 12), ADRs 0001 to 0004 in `docs/adr/`, the threat register, and the specs `CFX-APP-first-slice` and `CFX-APP-csv-profile`.

## Scope
In:
- Adding a PostgreSQL connection: host, port, database, user, password and TLS mode, with the password kept in the OS credential store.
- DuckDB's PostgreSQL extension, bundled in the wheel.
- Discovery of schemas, tables, partitioned tables, views and materialized views, with columns, types and declared keys.
- Choosing the objects to profile, and a paginated preview of a chosen one.
- A read-only session, and a warning the user confirms when the account can write.
- Fast and full profiles over the chosen objects, with a time limit per table and cancellation.
- The CSV profile's metrics, findings, sensitive-field rules, values on request and earlier runs, for each table.
- A Docker Compose PostgreSQL fixture with a synthetic database, for development and tests.

## Assumptions
- ASSUMPTION: one profile run covers every chosen object of one source, one after another, and there's one run at a time per runtime, as for a CSV.
- ASSUMPTION: a cancelled run's results don't appear as current, including the tables it finished before the cancel, as for a CSV and ADR 0002's "a run's status changes only once".
- ASSUMPTION: host, port, database name, user name, TLS mode and the CA certificate's path are saved in `workspace.db`. They aren't credentials. Only the password is a secret.
- ASSUMPTION: the connect timeout is 10 s, and every query DrivnX sends has a `statement_timeout`: 30 s for discovery, preview and values on request, and the per-table limit for a profile.
- ASSUMPTION: the per-table time limit can be set from 1 to 120 minutes for a run, and defaults to 10 minutes.
- ASSUMPTION: the write warning is asked again only when the chosen objects or the role's privileges change since the last confirmation.
- ASSUMPTION: a column's metrics are the CSV's set, except "values that don't fit the type", which can't happen in a typed column. Text lengths apply to text columns. `bytea`, arrays and JSON get counts only, and masked patterns are taken from text columns.
- ASSUMPTION: a declared primary key or unique constraint is shown on its column, and no "possible identifier" finding is raised for a column that already has one.
- ASSUMPTION: masked patterns always come from the fast profile's sample rows, even on a full profile, and are labelled that way, as a CSV's come from its first 100,000 rows.
- ASSUMPTION: the bundled extension covers the platforms CI tests: Linux x86-64, macOS arm64 and Windows x86-64. Elsewhere the screen says PostgreSQL isn't available on this platform.
- ASSUMPTION: supported servers are the PostgreSQL versions still supported by the community at release. Only the version the fixture pins is claimed as tested.
- ASSUMPTION: the performance targets under Non-functional are starting targets. Spike 2 met them on the same machine, and a network and a cold disk aren't measured.
- ASSUMPTION: the built screens meet WCAG 2.2 AA.

## User Stories
### US-01 — Connect a database without its password being kept in the workspace
### US-02 — See what's in the database and choose what to profile
### US-03 — Know DrivnX can't change my database, and be warned when my account could
### US-04 — Profile the chosen tables, fast or full, within limits I can stop
### US-05 — Read each table's metrics, findings and values as I would a CSV's
### US-06 — Go back to earlier runs and know what they describe
### US-07 — As a developer, prove read-only, the secrets rule, the storage rule and the targets against a real PostgreSQL

## Decisions and Contracts
Decided with the user in chat, 2026-11-13:
- **One spec for M4,** cut into tracer-bullet tickets, reusing the CSV profile's rules.
- **The PostgreSQL extension is bundled in the wheel.** DuckDB's signed `postgres_scanner` for each platform ships inside the package and is loaded from there, with signature checks on. Downloading extensions stays off ("Nothing may leave the machine or arrive from outside it", `packages/connectors/csv_files.py`). It is a 19 MB download and 57 MB installed on Linux, measured 2026-11-13, and pins `duckdb` to an exact version. Download on first use and psycopg were rejected.
- **No OS credential store:** the password is kept in memory until DrivnX stops, and asked for again after a restart. The screen says so. The `keyring` library reaches Keychain, Windows Credential Manager and Secret Service.
- **Sampling (ADR 0005, from spike 2):** a fast profile of a table, partitioned table or materialized view reads `TABLESAMPLE SYSTEM` with `REPEATABLE`, at 100,000 ÷ the server's row estimate, with the seed saved in the run. It took 0.034–0.039 s on 10 million rows, drew from across the whole table and repeated exactly. A table estimated at 100,000 rows or fewer, or never analysed, is read whole, with a guard at 100,001 rows. A view reads the first 100,000 rows it returns, because PostgreSQL refuses `TABLESAMPLE` on a view. `LIMIT` for tables was rejected: two runs on an unchanged table gave different rows, all from the oldest 30%. `BERNOULLI` was rejected because the server reads every page.
- **Values on request after a fast profile** use the run's method and seed. They're the same rows only while the table hasn't changed, and the label says so. Detecting changes from the server's counters was rejected.
- **A full profile uses one connection,** so a database administrator sees one DrivnX session. It took 4.0–4.2 s on 10 million rows, against 2.5–2.8 s with DuckDB's 7 or 8 parallel connections.
- **Read-only:** every session is read-only on DrivnX's side (`ATTACH ... (READ_ONLY)` and `default_transaction_read_only`). When the role can write to a chosen object, DrivnX names the objects and the user confirms before profiling. This is the "confirm read-only" step of the guided intake (Section 9). Refusing such roles was rejected because many users only have their application's account.
- **Objects:** ordinary tables, partitioned tables (the parent, as one table, with its partitions hidden), views and materialized views. Foreign tables are left out, because reading them queries another server. `pg_catalog`, `information_schema`, `pg_toast` and temporary schemas are always left out.
- **TLS:** libpq's `sslmode=prefer` by default, so a local database without TLS works. The screen shows whether the connection is encrypted, and the user can choose `require`, or `verify-full` with a CA certificate file.
- **Full profile limits:** a time limit per table. A table that reaches it is marked "stopped at the time limit, no results", the run goes on to the next table, and Cancel stops the whole run. DrivnX times each table and interrupts the scan itself, because DuckDB splits a scan into short `COPY` statements that `statement_timeout` never stops (ADR 0005).

Binding from earlier:
- The R3 rule (ADR 0004): only counts, percentages, text lengths and masked patterns are saved. Values are computed on request and never saved. Table, column and connection names aren't values from the data.
- ADR 0002: observations are written once, in transactions of at most 1,000, and a run's status changes only once. DuckDB spill files stay inside the workspace.
- First slice: no secret in SQLite, URLs, browser storage, logs or exports. Every `/api/*` route keeps the session, Host and Origin checks. GET routes change nothing.

API, following the Section 12 outline: `/api/sources` to add, test and discover a PostgreSQL source, save its selection and preview an object; `/api/profile-runs` and `/api/findings` as for a CSV.

## High-Level Design
- `packages/connectors` gains a PostgreSQL connector beside the CSV one: connect, test, discover, preview, and the read settings for a profile. It's the Section 12 `Connector` interface, written only as far as two sources need.
- `packages/profiling` computes metrics through DuckDB, as for a CSV, reading through the attached database.
- `packages/artifacts` saves the source without its password, the discovered objects, the selection and declared keys, through a migration.
- A small module wraps the OS credential store, with an in-memory fallback.

## Low-Level Design
- A source's `kind` gains `postgres`, and `path` can't stay `NOT NULL UNIQUE` for it, so the migration changes both.
- Each chosen object is a dataset named `schema.object`, and its columns are fields, so runs and observations work as they do for a CSV.
- Declared keys are saved as declared, apart from the candidate relationships that come with M5 (Section 5).
- A run's `scope` records the objects, each object's sample method, percentage and seed, the time limit, and each object's columns and types when it ran.
- DuckDB attaches the database with the password passed at connect time. The attach string is never logged or saved. DrivnX sets `application_name` to `DrivnX`, so a database administrator can see its sessions.

## Acceptance Criteria
- AC-01 (US-01): Given the Data Explorer, when the user adds a PostgreSQL connection, then they enter host, port (5432 by default), database, user, password and TLS mode (`prefer` by default, `require`, or `verify-full` with a CA certificate file), and there's no field for a connection string or URL.
- AC-02 (US-01): Given details that work, when the user tests or saves the connection, then DrivnX connects within the connect timeout and shows the server's version and whether the connection is encrypted, and a saved source appears in the Data Explorer under its database name and stays after a restart.
- AC-03 (US-01): Given a host that can't be reached, refused credentials, a database that doesn't exist, or TLS that is required and fails, when the user tests or saves, then a specific message says which, within the connect timeout, and no source is saved.
- AC-04 (US-01): Given an OS credential store, when a connection is saved, then its password is stored there under an entry naming DrivnX and the source, and DrivnX reads it from there after a restart.
- AC-05 (US-01): Given no OS credential store, when a connection is saved, then the screen says the password is kept only until DrivnX stops, and after a restart opening the source asks for it before anything is read.
- AC-06 (US-01): Given a stored password that the server now refuses, when the source is opened, then the user is asked for the password, and saving a new one replaces the stored one.
- AC-07 (US-01): Given the installed wheel on each tested platform, when DrivnX connects, then it loads the PostgreSQL extension from inside the package with DuckDB's signature check on, and DuckDB makes no request to any host other than the database server. Given another platform, then the screen says PostgreSQL isn't available on it.
- AC-08 (US-02): Given a connected source, then DrivnX lists every schema the user can see, except the system and temporary schemas, with its tables, partitioned tables (partitions hidden), views and materialized views, each with its kind and estimated row count labelled as an estimate. Foreign tables aren't listed.
- AC-09 (US-02): Given a listed object, when the user opens it, then its columns are shown with their PostgreSQL type, normalised type and whether they allow nulls, and its declared primary key, unique constraints and foreign keys (with the table and columns referenced), without reading any row.
- AC-10 (US-02): Given an object the user can't `SELECT` from, then it's listed as "no read access" and can't be chosen.
- AC-11 (US-02): Given the list, when the user chooses whole schemas or single objects and saves, then the selection stays after a restart, and only chosen objects can be previewed or profiled.
- AC-12 (US-02): Given a chosen object, when its preview opens, then it shows 50 rows at a time with next and previous, the rows aren't saved, the response is `Cache-Control: no-store`, and cells follow AC-28 and AC-29 of the first slice.
- AC-13 (US-02): Given an object dropped, or whose columns or types changed since discovery, when the user refreshes the source, then the list and the columns show the database as it is now. When a run starts on such an object, it is refused with a message pointing to the refresh.
- AC-14 (US-03): Given any connection DrivnX opens, then the session is read-only, and an attempt to write through it is refused by the server.
- AC-15 (US-03): Given the fixture's statement log, when the full journey runs, then DrivnX sent no statement that writes or changes the schema, and every statement carries `application_name` `DrivnX`.
- AC-16 (US-03): Given a role with `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE` on a chosen object, or a superuser, when the user starts a profile, then the screen names the objects or the superuser role and why, and the run starts only after the user confirms. Given a role that can only read the chosen objects, then no warning appears, and the screen says the account can only read them.
- AC-17 (US-04): Given chosen objects and no run in progress, then a second run is refused with a clear message, and the header's profile status and the Profile tab show which object a running run is on.
- AC-18 (US-04): Given chosen objects, when the user chooses Fast profile, then one run profiles each object: a table, partitioned table or materialized view estimated above 100,000 rows from a page sample of about 100,000 rows with a seed saved in the run, labelled "about N rows, sampled by page"; a smaller or never-analysed one whole, unless it returns more than 100,000 rows, when it's labelled as the first rows returned, not random; and a view from the first 100,000 rows it returns, labelled "first rows the view returned, not random".
- AC-19 (US-04): Given chosen objects, when the user chooses Full profile, then every row of each object is read, with exact results and each object's exact row count.
- AC-20 (US-04): Given a full profile and a per-table time limit, when an object's profile reaches the limit, then its scan stops within 5 s, no query from DrivnX is left running on the server, the object shows "stopped at the time limit, no results", the run goes on to the next object, and the finished run says which objects have no results.
- AC-21 (US-04): Given a run in progress, when the user cancels it, then it stops within the target under Non-functional, no query from DrivnX is left running on the server, its status is `cancelled`, and none of its results or findings appear as current.
- AC-22 (US-04): Given a connection lost, a server restart or a privilege revoked during a run, then the run ends `failed`, the message names the object and the reason, and its results don't appear as current.
- AC-23 (US-04): Given a profile job, then it creates nothing in the current directory, any spill files are inside `<workspace>/duckdb-tmp/`, and DrivnX holds at most one connection to the server for it, on a full profile too.
- AC-24 (US-05): Given a completed run, then for each column the Profile tab shows the metrics in the Assumptions, and each metric's scope (about N rows sampled by page, the first rows returned, or the whole object), its precision, and its run's time and ID. An estimated row count is never shown as exact.
- AC-25 (US-05): Given the synthetic fixture database, covering missing values, duplicates, dates and times with time zones, Unicode, numeric and decimal, boolean, JSON, UUID, arrays, `bytea`, high cardinality, a partitioned table, a view and a materialized view, when each object is profiled, then every saved metric equals its known expected value.
- AC-26 (US-05): Given a run that meets a finding's condition, then the CSV profile's findings appear (possible identifier, significant missing values, single-value column, possibly sensitive field, and on a full profile duplicate rows) with their evidence links, and a column with a declared primary key or unique constraint gets no possible-identifier finding.
- AC-27 (US-05): Given columns matching the sensitive-field name rules or the email, phone or payment card patterns, then each gets a possibly-sensitive finding, its values need the extra confirmation, and the screen says that no flag doesn't mean a column has no personal data, as AC-27 to AC-29 of the CSV profile.
- AC-28 (US-05): Given a column in a completed run, when the user asks for its values, then they're computed by querying the database with that run's method and seed, labelled with that scope, the time computed, and that they're the same rows only if the object hasn't changed since the run (for a view, that the rows may differ), cut at 1,000 characters, and sent with `Cache-Control: no-store`. Given the database unreachable, then a clear message shows, and the saved metrics still show.
- AC-29 (US-05): Given a full journey with distinctive planted values in the fixture, including the preview and values on request, then a scan of every file in the workspace, during the run and after DrivnX stops, and of its logs at their most detailed finds no value from the database, except masked patterns and the names of schemas, objects, columns and the connection.
- AC-30 (US-01): Given a full journey with a distinctive planted password, then a scan of every file in the workspace, of the logs at their most detailed, of browser storage and of every URL the browser requested finds no part of the password.
- AC-31 (US-06): Given several runs of a source, then the user can list them with mode, status, time and objects, open any completed one, and the newest completed run is shown by default.
- AC-32 (US-06): Given results, then they say they describe the object at the run's time. Given an object whose columns or types differ from the run's, then the Profile tab says the results are out of date.
- AC-33 (US-06): Given a restart, then completed runs, their metrics and findings are as they were, a run that was in progress shows as `interrupted`, and no query DrivnX started is still running on the server.
- AC-34 (US-07): Given `testdata/` and the fixture, then the fixture is a Docker Compose service with a pinned image, its database is built by a committed script from synthetic data only, and tests that need it say so when it isn't running.
- AC-35 (US-07): Given the installed wheel and the fixture, then the journey of adding a connection, confirming the write warning, choosing objects, running a fast profile, running a full profile and opening a finding's evidence passes in Playwright from one origin, with an automated accessibility check on the new screens in each state.
- AC-36 (US-07): Given the threat register and the ADRs, then the "Database or API key leaks" row records what's built and tested, and ADRs record the sampling method from spike 2, the bundled extension and the credential store.

## Non-functional
- **Performance (ASSUMPTION, starting targets):** against the fixture on the same machine, listing a database of 1,000 objects takes at most 5 s, opening an object's columns at most 2 s, a fast profile of a 10-million-row, 5-column table at most 10 s, and cancelling stops a run within 5 s. A full profile is measured and recorded, and bounded by the time limit. Spike 2 measured 3–4 ms to list 1,004 objects, about 0.5 s for a fast profile, 0.10–0.14 s to cancel, and 4.0–4.2 s for a full profile over one connection (ADR 0005). A network between DrivnX and the server isn't measured, and over one a full profile is bounded by moving the table.
- **Security:** DrivnX never writes to a source. No secret is saved outside the OS credential store. No value from the database is saved except as the R3 rule allows. DrivnX talks only to the database the user connected. New routes keep the session, Host and Origin checks.
- **Accessibility (ASSUMPTION):** new screens meet WCAG 2.2 AA, work by keyboard alone, and don't rely on colour alone for scope, precision, encryption or trust state.
- **Compatibility:** only what can be run here and in CI is claimed as tested.

## Verification
- Each criterion is covered by a test that names its ID, such as `CFX-APP-postgres/AC-01`, except that AC-36 is checked by reading the documents.
- pytest against the fixture for the connector, profiling, storage and API; Vitest for components; Playwright for AC-35 against the installed wheel.
- Spike 2 is done (ADR 0005). `tests/measure_postgres_sample.py` reproduces it, and the ticket that builds the fixture runs it there.
- The credential store is tested on each OS CI runs, and the in-memory fallback where no store is available.

## Dependencies
- `CFX-APP-csv-profile`, done and merged (#15).
- ADRs 0001 to 0005, and the threat register.
- Docker on the development machine for the fixture.

## Out of Scope
Other databases (SQL Server and MySQL are next candidates, Section 5). Foreign tables. Connection strings and URLs, `.pgpass` and service files, SSH tunnels, client certificates, Kerberos and cloud sign-in tokens. Removing a source, which no source can do yet. Writing to a source in any way.
Later milestones: candidate relationships and the overview (M5), dictionary screens (M6), contracts (M7), AI (M8), exports (M7 and M9).

## Open Questions
None blocks approval.
- **CI on macOS and Windows.** GitHub's macOS runners have no Docker, and Windows runners can't run Linux containers, so the fixture runs there differently or not at all. To settle in planning. Until then, PostgreSQL tests there are reported as NOT RUN.
- **A stored password after its source is gone.** No source can be removed yet, so an entry stays in the OS credential store until the user deletes it. Recorded as a limit until removing a source is built.

## Conflicts with Current System
- **`source` table** (`packages/artifacts/migrations/versions/0003_sources.py`): `kind CHECK (kind IN ('csv'))` and `path TEXT NOT NULL UNIQUE` don't fit a database, so a migration changes them.
- **`pyproject.toml`:** `duckdb>=2.0.0` must become an exact pin to match the bundled extension. `keyring` is a new dependency. The wheel build (`hatch_build.py`) must add the extension files.
- **Declared keys:** Section 5's metadata scan includes declared keys, and no table holds them yet, so the migration adds one.
- **CSV profile Non-functional, "Nothing leaves the machine":** a PostgreSQL source sends queries to the database the user connected. Section 7's "profiling and artifact storage run locally" still holds.
- **First slice, deferred items:** the OS credential store and the Docker PostgreSQL fixture were deferred to M4, and arrive here.
- **Threat register, "Database or API key leaks":** status "Planned" changes as this is built.
- **Section 5, "sample by default":** holds for tables (ADR 0005). A view reads its first rows, so Section 5 needs a note, like the CSV's "first rows, not random". Section 11's open "PostgreSQL sampling" decision is closed by ADR 0005.

## Revision History
- Revision 1: Initial draft from the user's decisions on 2026-11-13; not approved. AC-18 waits for spike 2.
- Revision 2: Spike 2 done and ADR 0005 recorded, from the user's decisions on 2026-11-13. AC-18 is completed: page samples with a seed for tables, the first rows for views. AC-20 says the scan stops within 5 s, as DrivnX enforces the limit itself. AC-23 holds a full profile to one connection too. AC-24 names the new scopes. AC-28 says values on request repeat the run's rows only while the object is unchanged. The extension's size is corrected to a 19 MB download and 57 MB installed. Approved by the user in chat, 2026-11-13.
