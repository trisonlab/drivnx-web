---
id: DRIVN-60
external_key: DRIVN-60
kind: feature
title: "Connect XLSX and Parquet files, correct a CSV's column types, and find files by browsing"
summary: "Connect an XLSX workbook (choosing its sheets) or a Parquet file and get the CSV profile's metrics, findings, values on request and earlier runs; correct a CSV column's type; pick files with a file browser; and keep Recent and Pinned items in the Data Explorer."
owner: drivnx-web
lifecycle: approved
revision: 1
approved_revision: 1
approval_evidence: "user approved revision 1 with its four decisions (mixed-column type, content over name, the decompression limit, the messages) in chat, 2026-11-22"
updated: "2026-11-22"
related: [DRIVN-8, DRIVN-9, DRIVN-45]
status: CONFIRMED
---

# Connect XLSX and Parquet files, correct a CSV's column types, and find files by browsing

## Problem
DrivnX profiles a CSV and a PostgreSQL database, but much of the data its users are handed comes as Excel workbooks or
Parquet files, and Phase 1 promises both (direction doc, Sections 1, 2 and 5). This is the rest of M2, which the user
moved after M3 for CSV (Section 10's note of 2026-11-05). It also brings what the CSV slice deferred to it: correcting a
CSV column's inferred type. And it makes files easier to find: a file browser instead of typing a path, and Recent and
Pinned items in the Data Explorer.

Today every file a user connects is read as a CSV, whatever it is (`connect_source` calls `scan_csv`,
`apps/local-api/local_api/sources_api.py:209`), so a workbook or a Parquet file gives nonsense or an error.

Source of truth: `../drivnx-docs/docs/DrivnX_Phase1_Development_Direction.md` (Sections 1, 2, 5, 7 and 10), ADRs 0002 to
0006 in `docs/adr/`, the threat register, the specs `DRIVN-9` and `DRIVN-45`, and the research note
`.ai/knowledge/dependencies/duckdb-xlsx-parquet.md` (INFERRED: its XLSX behaviour comes from DuckDB's docs and source,
not from a run).

## Scope
In:
- Connecting an `.xlsx` workbook: its worksheets listed, the sheets to profile chosen, and each chosen sheet's range and
  header detected and correctable.
- Connecting a single `.parquet` file, with its metadata read before any row is scanned.
- DuckDB's `excel` extension, bundled in the wheel as `postgres_scanner` is (ADR 0006). Parquet is built into DuckDB.
- A paginated preview, fast and full profiles with cancellation, the CSV profile's metrics, findings, sensitive-field
  rules, values on request and earlier runs, for each chosen sheet and each Parquet file.
- Correcting a CSV column's type.
- A file browser for choosing a CSV, XLSX or Parquet file.
- Recent and Pinned items in the Data Explorer.
- Golden fixtures for XLSX and Parquet.

## Assumptions
- ASSUMPTION: a workbook is one source and each chosen sheet is a dataset, as a database's objects are
  (`DRIVN-45`). One profile run covers every chosen sheet, one after another, and there's one run at a time per
  runtime.
- ASSUMPTION: a sheet's metrics are the CSV's set (`DRIVN-9`, the Assumptions), with "values that don't fit"
  counted only for a column that fell back to text. A Parquet column keeps its native type, so, as for a PostgreSQL
  column, it never has values that don't fit. Text lengths and masked patterns apply to text columns; lists, structs,
  maps, binary and JSON get counts only.
- ASSUMPTION: masked patterns always come from the fast profile's rows, even on a full profile, and are labelled that
  way, as for a CSV and a database.
- ASSUMPTION: a cell's value is its saved, last calculated result: a formula's text is never read, and a formula that
  was never calculated reads as empty. A merged cell's value is in its top-left cell, and the others are empty.
- ASSUMPTION: hidden sheets are listed and marked hidden, and chart sheets aren't listed, as they hold no cells.
- ASSUMPTION: Parquet's metadata statistics (`stats_min`, `stats_max`) are real values, so under the R3 rule (ADR 0004)
  they're never shown or saved. Its exact row count and null counts are counts, saved with scope `metadata`.
- ASSUMPTION: an encrypted Parquet file, a folder or a glob of Parquet files, and a remote path (`s3://`, `https://`)
  are refused, naming why.
- ASSUMPTION: the file browser shows at most 1,000 entries of a folder, and says how many more there are.
- ASSUMPTION: Parquet lists, structs, maps and binary columns are `string` in ADR 0003's vocabulary, which has no type
  for them, and show their native type (AC-09).
- ASSUMPTION: a path with `*`, `?` or `[` is read as written, never as a pattern, as for a CSV, so `part-*.parquet`
  opens only a file of that name.
- ASSUMPTION: the bundled `excel` extension covers the platforms CI tests: Linux x86-64, macOS arm64 and Windows
  x86-64. Elsewhere the screen says XLSX isn't available on this platform, and CSV, Parquet and PostgreSQL still work.
- ASSUMPTION: the performance targets under Non-functional are starting targets, measured and recorded.
- ASSUMPTION: the built screens meet WCAG 2.2 AA.

## User Stories
### US-01 — Connect a workbook, choose its sheets, and correct how a sheet is read
### US-02 — Connect a Parquet file and see what's in it before anything is scanned
### US-03 — Preview and profile a sheet or a Parquet file as I would a CSV
### US-04 — Correct a CSV column's type when detection got it wrong
### US-05 — Pick a file by browsing instead of typing its path
### US-06 — Get back to what I use: Recent and Pinned
### US-07 — As a developer, prove the readers, the bundled extension, the storage rule and the targets

## Decisions and Contracts
Decided with the user in chat, 2026-11-22:
- **One spec for the rest of M2:** XLSX, Parquet, CSV column types, the file browser, and Recent and Pinned, cut into
  tracer-bullet tickets, reusing the CSV profile's rules.
- **XLSX is read by DuckDB's `excel` extension, bundled in the wheel** as ADR 0006 bundles `postgres_scanner`: signed,
  per platform, loaded from the package with signature checks on, never downloaded. It adds about 22.6 MB to the wheel
  and 74 MB installed (the research note; Windows' binary alone is 46.7 MB). A Python reader was rejected as a second
  reading path beside DuckDB's.
- **A workbook is one source, and the user chooses its sheets,** each with its own detected range and header, which the
  user can correct.
- **A column whose cells don't all fit one type falls back to text,** says so, and counts the cells that fit the
  inferred type as values that don't fit, as a CSV does. A sheet's other columns keep their types. DuckDB types a
  column from its first data row only, and fails the read on a later cell that doesn't cast, so DrivnX types each
  column from more rows than that.
- **An XLSX fast profile reads each chosen sheet's first 100,000 rows,** labelled "first rows, not random", as a CSV's:
  a sheet streams from its start and has no row count without reading it all. A full profile reads every row.
- **A Parquet file's fast profile reads about 100,000 rows from row groups chosen with the run's seed,** labelled
  "about N rows, sampled by row group", as a database's table is sampled by page. Its exact row count comes from its
  metadata. Measured on 40 million rows: 0.04–0.09 s, against 3–4 s for a random row sample, which reads the whole
  file. A full profile reads every row.
- **One Parquet file per source.** A folder of Parquet files, as Spark writes them, comes in a later spec.
- **A CSV column's type can be corrected to integer, number, boolean, date, time, timestamp or text,** ADR 0003's
  vocabulary, which DrivnX already infers. A date or timestamp takes its format from a list: YYYY-MM-DD, DD/MM/YYYY,
  MM/DD/YYYY and DD.MM.YYYY, with a time of HH:MM or HH:MM:SS for a timestamp, and the detected format first.
- **A type correction is a correction:** as for a header or delimiter (`DRIVN-9` AC-36), earlier runs are
  kept, labelled "used earlier settings", their findings stop being current, and the next profile uses the new types.
- **The file browser** starts in the home folder and shows folders and `.csv`, `.xlsx` and `.parquet` files, with
  their size and modified time, and leaves hidden ones out. It lists any folder the user can read. Typing a path stays.
- **Recent and Pinned are kept per workspace:** Recent holds the 10 sources, sheets or database objects opened last;
  Pinned holds any the user pins, until unpinned.
- **Starting targets** as the CSV's, scaled for XLSX's one-thread, unzipping reader (Non-functional).

Decided with the user in chat, 2026-11-22, approving revision 1:
- **A mixed column takes the type most of its non-empty cells share, and a tie is text** (AC-04). Every cell of
  another type counts as not fitting.
- **A file is read by what its content is,** a ZIP workbook, Parquet's magic bytes or text, not by its name (AC-29).
- **The decompression limit:** a workbook whose parts would decompress to more than 2 GB, or to more than 200 times
  their compressed size, is refused before any sheet is read (AC-06), as a workbook is a ZIP of XML files and the
  reader holds a sheet's shared strings in memory. The first XLSX ticket measures it.
- **The messages** quoted in the Acceptance Criteria are agreed as written.

Binding from earlier:
- ADR 0002: observations are written once and never changed, a run's status changes only once, and DuckDB's spill
  files stay inside the workspace. Files are referenced in place, never copied (Section 5).
- ADR 0004 and the threat register's R3: only counts, percentages, text lengths and masked patterns are saved; other
  values are computed on request and never saved.
- `DRIVN-9`: a row detection used as column names may hold personal data, so names replaced by a
  correction are replaced in the saved data too (AC-22). A sheet's header correction follows the same rule.
- `DRIVN-45` (DRIVN-57): earlier runs are listed and opened, the newest completed one is shown by default, and
  results say when they're out of date.

## High-Level Design
- `packages/connectors` gains an XLSX reader and a Parquet reader beside the CSV one: detect, list sheets or read
  metadata, preview, and read for a profile, all through DuckDB.
- `packages/profiling` measures through DuckDB, as for a CSV and a database.
- `packages/artifacts` saves the new kinds of source, a workbook's sheets with their selection and read settings, a CSV
  column's corrected type, and Recent and Pinned items, through migrations.
- The local API gains a route that lists a folder for the file browser, and routes for Recent and Pinned.

## Low-Level Design
- A source's `kind` gains `xlsx` and `parquet`; a source is still one per path.
- Each chosen sheet is a dataset named after the sheet, its columns are fields, and its range and header are its read
  settings, so runs, observations and corrections work as for a CSV.
- A run's `scope` records each sheet's range and header, or the Parquet file's row groups and seed, and each column's
  type when it ran.
- The workbook's sheet list comes from its `xl/workbook.xml`, as DuckDB's reader has no way to list sheets (the
  research note).
- A CSV column's corrected type and date format are part of its read settings, so a correction replaces its dataset
  (ADR 0002).

## Acceptance Criteria
Examples use `sales.xlsx` (sheets `Orders`, 250,000 rows; `Customers`, 900 rows, hidden; and a chart sheet `Trend`),
`events.parquet` (40,000,000 rows in 326 row groups, written by `parquet-cpp-arrow version 17.0.0`) and `orders.csv`.
A quoted message is shown as written; none repeats the path or a value from the file.

- AC-01 (US-01): Given `sales.xlsx`, when the user connects it, then the Data Explorer shows the source `sales.xlsx` with the sheets `Orders` and `Customers (hidden)` in workbook order, `Trend` isn't listed, and only `Orders` is chosen.
- AC-02 (US-01): Given `sales.xlsx` with `Orders` and `Customers` chosen and saved, when DrivnX restarts, then both are still chosen. When the user previews or profiles a sheet that isn't chosen, then it's refused with "Choose this sheet to preview or profile it."
- AC-03 (US-01): Given a sheet with the title "Q3 sales" in A1 and its table in B3:F200, when the user corrects the range to `B3:F200` with the first row as the header and saves, then the columns are the names in B3:F3 and the preview starts at row 4. Given a range that isn't one, such as `F200:B3`, `B3` or `B3:F`, then saving is refused with "Enter a range like B3:F200, from its top-left cell to its bottom-right cell." and the previous settings stay. Given no header, then the columns are named `column0`, `column1` and so on, as a CSV's are.
- AC-04 (US-01): Given a column `Amount` with the cells 10, 12.5, `n/a` and 7, then it's read as text, its column says "Mixed values, read as text", and a profile counts 1 of its 4 cells as not fitting the type `number`. Given a column of cells formatted as dates, then it's a date column, and a column of TRUE and FALSE is a boolean one. A column whose first cell is empty is typed from the cells that aren't.
- AC-05 (US-01): Given a cell that Excel shows as 22/11/2026 in a 1904-date workbook and one in a 1900-date workbook, then DrivnX shows and profiles both as 2026-11-22.
- AC-06 (US-01): Given one of these files, when the user connects it, then it's refused with its message and nothing is saved: an `.xls` file, "This is an older Excel file (.xls), which DrivnX can't read. Save it as .xlsx in Excel, then connect it again."; a password-protected workbook, "This workbook is password-protected, so DrivnX can't read it. Save a copy without a password, then connect that."; a file named `.xlsx` that isn't a workbook, "This file isn't an Excel workbook, so it can't be read as one."; a workbook over the decompression limit, "This workbook would take more than 2 GB to unpack, so DrivnX won't read it."
- AC-07 (US-02): Given `events.parquet`, when the user connects it, then within 1 s its columns with their native types, "40,000,000 rows", "326 row groups" and "Written by parquet-cpp-arrow version 17.0.0" are shown, read from its metadata without scanning a row. No response and no row of `workspace.db` holds a minimum or maximum from its statistics.
- AC-08 (US-02): Given one of these paths, when the user connects it, then it's refused with its message and nothing is saved: an encrypted Parquet file, "This Parquet file is encrypted, so DrivnX can't read it."; a folder, "This path is a folder. Enter the path to a CSV, XLSX or Parquet file; a folder of Parquet files isn't supported yet."; `s3://bucket/events.parquet` or `https://example.com/events.parquet`, "DrivnX reads files on this computer only. Enter the full path to a file."; a file named `.parquet` that isn't one, or one cut short, "This file isn't a Parquet file, so it can't be read as one."
- AC-09 (US-02): Given Parquet columns of the types `UBIGINT`, `DECIMAL(38,2)`, `TIMESTAMP_NS`, `TIMESTAMP WITH TIME ZONE`, `UUID`, `BLOB`, `INTEGER[]`, `STRUCT(a INTEGER)` and `MAP(VARCHAR, INTEGER)`, then each column shows its native type and its type in ADR 0003's vocabulary: integer, number, timestamp, timestamp, string, string, and string for the list, struct and map, which also say "list", "struct" and "map".
- AC-10 (US-03): Given a chosen sheet or a Parquet file, when the user opens its preview, then 50 rows a page are shown (up to 200 when asked), each cell cut at 1,000 characters, every response sent with `Cache-Control: no-store`, and no row of `workspace.db` holds a value from the preview.
- AC-11 (US-03): Given `sales.xlsx` with both sheets chosen, when the user starts a fast profile, then one run reads `Orders`' first 100,000 rows and then all 900 of `Customers`, each labelled "first rows, not random". When the user starts a full profile, then the run reads every row and shows "250,000 rows" and "900 rows".
- AC-12 (US-03): Given `events.parquet` and a run with the seed 42, when the user starts a fast profile, then it reads about 100,000 rows from row groups chosen with that seed, labelled "about 100,000 rows, sampled by row group", and the row count "40,000,000 rows" is labelled "from the file's metadata". A second fast run with the seed 42 reads the same rows. A full profile reads all 40,000,000.
- AC-13 (US-03): Given a full profile of a 1,000,000-row sheet or a 1 GB Parquet file, when the user cancels it 3 s after it starts, then it stops within 5 s, its status is `cancelled`, and none of its results or findings appear as current.
- AC-14 (US-03): Given a completed run of `Orders` or `events.parquet`, then each column shows the CSV profile's metrics with their scope and precision; the CSV profile's findings and sensitive-field rules apply; values on request are computed now from the run's own rows (the sheet's first rows, or the same seeded row groups) and never saved; and earlier runs are listed and opened as a CSV's. When the file's size or modified time has changed since the run, then the Profile tab says "This file has changed since this profile ran, so these results may be out of date."
- AC-15 (US-03): Given the golden fixtures profiled fast and full, with values on request opened for every column, then a raw scan of `workspace.db` and its WAL file finds no cell value from them, only counts, percentages, text lengths, masked patterns, column names and sheet names.
- AC-16 (US-04): Given `orders.csv` whose column `order_date` holds "22/11/2026" and was detected as text, when the user sets its type to date with the format DD/MM/YYYY and saves, then the column shows "date", and the next profile measures it as a date and counts "2026-13-01" as 1 value that doesn't fit. The format list shows the detected format first. Choosing "As detected" puts the detected type back.
- AC-17 (US-04): Given a run of `orders.csv` made before that type correction, then it's kept, labelled "Used earlier settings", and its findings say they aren't current. The column names aren't replaced, as a type correction doesn't change them.
- AC-18 (US-05): Given a home folder holding `Documents/`, `.ssh/`, `events.parquet`, `notes.txt` and `sales.xlsx`, when the user opens the file browser, then it shows `Documents/`, then `events.parquet` and `sales.xlsx` with their size and modified time; `.ssh/` and `notes.txt` aren't shown. A folder or file the system marks hidden isn't shown either. On Windows, the browser also lists the drives, such as `C:\` and `D:\`. Choosing `sales.xlsx` connects it, as typing its path does.
- AC-19 (US-05): Given a folder of 1,500 entries it would show, then the first 1,000 are shown, folders first and then by name, and "500 more aren't shown. Type the path to open one of them." Given a folder DrivnX can't read, when the user opens it, then "DrivnX can't open this folder." is shown and the browser stays on the folder it was in.
- AC-20 (US-05): Given the folder listing route, then a request without the session, with another Host or with another Origin is refused as every other route's is; a response holds names, kinds, sizes and modified times only, never a byte of a file; and listing folders writes nothing to `workspace.db`.
- AC-21 (US-06): Given 12 items opened one after another (sources, sheets and database objects), then Recent lists the last 10, newest first, also after a restart. Opening an item already in Recent moves it to the top, so it's listed once. Opening an item from Recent goes to it.
- AC-22 (US-06): Given `Orders` pinned, then it stays in Pinned after a restart, until the user unpins it. Given a pinned or recent item whose file has been deleted, or a database object a refresh replaced, then it stays in its list, says "Unavailable", and opening it says why.
- AC-23 (US-07): Given the wheel, then it holds DuckDB's signed `excel` extension for Linux x86-64, macOS arm64 and Windows x86-64, loaded from the package with signature checks on and never downloaded, and the wheel's size with it is recorded. On another platform, connecting a workbook is refused with "XLSX isn't available on this platform (linux_arm64)." and CSV, Parquet and PostgreSQL still work.
- AC-24 (US-07): Given an XLSX golden fixture (typed columns, a mixed column, dates, booleans, empty cells, Unicode text, a hidden sheet, and a second workbook with 1904 dates) and a Parquet golden fixture (each type in AC-09, nulls, Unicode text, several row groups), made by a script independent of DrivnX's readers, then every saved metric equals its known value.
- AC-25 (US-07): Given the threat register and the ADRs, then the "Huge or malformed input" row and R3 record what's built and tested for XLSX and Parquet, and ADRs record the bundled `excel` extension and how a sheet is typed, and how a Parquet file is sampled.
- AC-26 (US-01): Given `sales.xlsx` already connected with a corrected range, when the user connects the same path again, then the existing source opens with its sheets, selection and settings.
- AC-27 (US-01): Given a profile run in progress, when the user saves a sheet's range or header, or a CSV column's type, then it's refused with "A profile is running. Wait for it to finish, then save these settings again." and the previous settings stay.
- AC-28 (US-01): Given a workbook with no worksheet, only chart sheets, when the user connects it, then it's refused with "This workbook has no sheets with cells to read." and nothing is saved.
- AC-29 (US-01): Given a file named `orders.csv` whose content is an XLSX workbook, or `sales.xlsx` whose content is CSV text, then DrivnX reads it by what its content is, not its name, and the source says which it was read as.
- AC-30 (US-03): Given a Parquet file of 60,000 rows, when the user starts a fast profile, then it reads every row, labelled "every row, 60,000 rows". Given a file of 5,000,000 rows in one row group, then a fast profile still reads about 100,000 rows, sampled within that row group.
- AC-31 (US-03): Given a sheet or a Parquet file that moved, was deleted or can't be read, when a run starts or fails partway, then the run ends `failed` with "DrivnX can't find this file. It may have moved or been deleted." or "DrivnX couldn't read this file.", and its results don't appear as current.
- AC-32 (US-05): Given the file browser, when the user goes up from the home folder, then it shows the parent folder, up to the top of the drive or `/`. Given a link to a folder, then it opens the folder the link points to.

## Non-functional
- **Performance (ASSUMPTION, starting targets):** on a development laptop's disk, a 1 GB, 5-column Parquet file: a fast
  profile at most 5 s, a full profile at most 60 s. A 1,000,000-row, 5-column sheet: a fast profile at most 10 s, a full
  profile at most 120 s. Cancelling stops a run within 5 s, and the file browser lists a 1,000-entry folder within 1 s.
  Figures are measured and recorded, as in the first slice. A workbook's memory with a large shared-strings table is
  measured too (spike 1).
- **Security:** no value from a file is saved except as the R3 rule allows. Nothing leaves the machine. Files are read
  in place and never copied. New routes keep the session, Host and Origin checks, and the file browser lists only what
  the user's account can read.
- **Accessibility (ASSUMPTION):** new screens meet WCAG 2.2 AA, work by keyboard alone, and don't rely on colour alone
  for scope, precision, availability or trust state.
- **Compatibility:** only what can be run here and in CI is claimed as tested.

## Verification
- Each criterion is covered by a test that names its ID, such as `DRIVN-60/AC-01`, except that AC-25 is
  checked by reading the documents.
- pytest for the readers, profiling, storage and API; Vitest for components; Playwright against the installed wheel for
  connecting, browsing, choosing sheets and profiling.
- Golden fixtures are generated by a script kept in the repo, so their known values are independent of DrivnX's reader.
- The bundled extension is loaded and checked on each OS CI runs.

## Dependencies
- `DRIVN-9`, done; `DRIVN-45`, whose earlier-runs work (DRIVN-57) is merged.
- ADRs 0002 to 0006, and the threat register.
- The research note `.ai/knowledge/dependencies/duckdb-xlsx-parquet.md`.

## Out of Scope
`.xls`, `.xlsm` macros, `.ods` and other spreadsheet formats; writing to a workbook in any way. A folder of Parquet
files, partitioned datasets, Delta Lake and Iceberg; remote paths. Encrypted Parquet files and password-protected
workbooks. Formulas' text, comments, charts, pivot tables, conditional formats and colours. Removing a source, which no
source can do yet. Watching a folder for new files.
Later milestones: candidate relationships and the overview (M5), dictionary screens (M6), contracts (M7), AI (M8),
exports (M7 and M9).

## Open Questions
None blocks approval.
- **Every XLSX behaviour the spec relies on** comes from DuckDB's docs and source, not from a run (the research note).
  The first XLSX ticket loads the bundled extension and checks them, and a behaviour that differs comes back here as a
  new revision.
- **A Parquet file with one huge row group:** the fast profile then reads slices of rows inside it, which the research
  note measured as cheap (0.085 s for eight slices). To confirm on the golden fixture.

## Conflicts with Current System
- **`connect_source`** (`apps/local-api/local_api/sources_api.py:209`) scans every file as a CSV, `SourceResponse.kind`
  is `Literal["csv"]` (`:80`), and its messages say "CSV" (`:167`, `:172`), so a workbook or a Parquet file must be
  told apart first, by its content as well as its name.
- **`source` table** (`packages/artifacts/migrations/versions/0007_postgres_sources.py:38`): `kind CHECK (kind IN
  ('csv', 'postgres'))`, so a migration adds `xlsx` and `parquet`.
- **`normalised_type`** (`packages/connectors/csv_files.py:152`) maps types it doesn't know, such as `TIMESTAMP_NS`,
  `UUID` and `UINTEGER`, to `string`, which AC-09 needs to change for Parquet.
- **The wheel** (`hatch_postgres.py`, ADR 0006) bundles only `postgres_scanner`; the `excel` extension is added the
  same way, with its pinned checksums re-downloaded in the build ticket.
- **Section 5, "XLSX: all-text fallback":** a column falls back to text, not the whole sheet, as decided. Section 5
  needs a note.
- **`DRIVN-9`, Decisions and Contracts:** "Column types move to the 'other local sources' slice" is met here
  (AC-16, AC-17).
- **Threat register:** "Huge or malformed input" and R3 are built and tested for CSV (and PostgreSQL) only; their rows
  change as this is built (AC-25).

## Revision History
- Revision 1: From the user's decisions in chat on 2026-11-22, with concrete examples, edge cases (AC-26 to AC-32)
  and messages. Approved by the user in chat, 2026-11-22, with the mixed-column type, content over name, the
  decompression limit and the messages decided.
