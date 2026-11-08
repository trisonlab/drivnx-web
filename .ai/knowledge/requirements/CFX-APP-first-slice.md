---
id: CFX-APP-first-slice
external_key: DRIVN-8
kind: feature
title: "First vertical slice: runtime to CSV preview, on the M1 domain model"
summary: "Start DrivnX, set up a workspace in the browser, connect a CSV and see its metadata and a paginated preview, built on the full M1 domain model."
owner: drivnx-web
lifecycle: approved
revision: 9
approved_revision: 9
approval_evidence: "user approved revision 9 in chat, 2026-11-04 (chose 'Event now, detection later' and 'Add a keep-as-is action' on building T08)"
updated: "2026-11-04"
related: []
status: CONFIRMED
---

# First vertical slice: runtime to CSV preview, on the M1 domain model

## Problem
Nothing has been built yet. Before profiling depth is added, DrivnX has to prove three things end to end: the local
runtime and browser model, the security baseline, and the domain model (Section 12 of the direction doc). This slice
covers M1 and the start of M2.

Source of truth: `../docs/DrivnX_Phase1_Development_Direction.md`, plus ADRs 0001, 0002 and 0003 in `docs/adr/`.

## Scope
In:
- Runtime launcher, session handshake and request protection (ADR 0001).
- Workspace creation, first-launch setup and reopening.
- All M1 entities and trust-state rules, saved in SQLite (ADR 0002), including the contract rule model (ADR 0003).
- Connecting a CSV by its path, automatic detection, a metadata scan and a paginated preview.
- The app shell, with placeholders for what isn't built.
- Python and frontend tooling with lockfiles, synthetic test data, and CI configuration.

The review, dictionary and contract parts of the domain model have tests but no screens in this slice.

## Assumptions
- ASSUMPTION: Linux, macOS and Windows are all targets, but only Linux can be run during this work. The other two stay NOT RUN until CI can run them.
- ASSUMPTION: the UI is English only.
- ASSUMPTION: the look is plain, with the orange accent from `../docs/design-mocks/` and no design polish. The orange accent must stay distinct from the Inferred trust colour (orange / amber, Section 3).
- ASSUMPTION: a preview page holds 50 rows, the API allows at most 200, and a preview cell shows at most 1,000 characters and is visibly marked when cut short.
- ASSUMPTION: detection covers comma, semicolon, tab and pipe delimiters; UTF-8 (with or without a BOM), UTF-16 with a BOM in either byte order, Latin-1 and Windows-1252 encodings; and files with or without a header row. Files without a header get generated column names, shown as generated.
- ASSUMPTION: Windows-1252 files, big-endian UTF-16 files and little-endian UTF-16 files with characters above U+FFFF are limited to 100 MB, because DuckDB can't read them directly and they have to be decoded in memory (measured at about 530 MB of RAM for a 94 MB file).
- ASSUMPTION: connecting a path that is already a source in the workspace opens the existing source instead of adding a duplicate.
- ASSUMPTION: the performance budgets under Non-functional are starting targets, to be confirmed by spike 1.
- ASSUMPTION: the built screens meet WCAG 2.2 AA.

## User Stories
### US-01 — Start DrivnX with one command and land in the app in my browser
### US-02 — Know that other web pages and programs on my machine can't use my session through the browser
### US-03 — Set up a workspace, and find it again as I left it
### US-04 — Connect a CSV by giving its path
### US-05 — See what DrivnX found in the file without it reading every row
### US-06 — Page through the file's rows without any of them being saved
### US-07 — See the app's layout and my privacy state, and tell what isn't built yet
### US-08 — Trust that review state and its history can't be corrupted or silently changed
### US-09 — As a developer, install, build, test and check the project from locked dependencies

## Decisions and Contracts
Decided with the user in chat, 2026-11-01 ("go with your recommendations"):
- **M1 breadth:** build all M1 entities and trust-state rules now, with the tests ADR 0002 lists.
- **Choosing a CSV:** the user types or pastes the full path. A file browser comes later in M2, because a browser's own file picker doesn't reveal a file's real path.
- **CSV depth:** detect and show the result. Correcting detection and column types comes later in M2.
- **Metadata scan:** file name, size, modified date, columns and detected types are saved. Preview rows are never saved. No row count until profiling (M3).
- **Encodings, decided 2026-11-02:** DuckDB doesn't detect encodings, so DrivnX does, by reading the file once. It refuses Windows-1252 outright and reads big-endian UTF-16 as garbage, so those two are decoded in Python and limited to 100 MB. Reviewing T05 on 2026-11-03 found that DuckDB also refuses characters above U+FFFF in little-endian UTF-16, so DrivnX checks those files in full and decodes the ones that have them in the same way. The `encodings` extension was rejected: 276 MB per platform, and no big-endian UTF-16. A temporary converted file was rejected because it copies source data (Section 5).
- **Headers that can't be told from data, decided 2026-11-03:** a first row that doesn't fit the column types looks like a header, and commas in the text look like a delimiter whether or not the file has a header. No rule separates these files from ones with a real header or a title line, so DrivnX asks DuckDB twice, strictly and leniently, and accepts that one row may become the column names or the delimiter may be wrong (AC-22, AC-26). The screen says a row from the file was used. Correcting either comes later in M2.
- **Lines skipped above the table, decided 2026-11-03:** DuckDB skips leading lines that don't match the rest of the file. That is what makes a title line work, but it can't tell a title line from real rows that have fewer fields. The preview keeps skipping, so a file with a title line keeps the right columns, and says how many lines aren't shown (AC-27). Those lines can't be reached until detection can be corrected later in M2.
- **Quotes first seen late in a file, decided 2026-11-03:** DuckDB decides how a file is quoted from a sample of its start, so a first quoted value after that sample is misread. If the sample shows no quotes, the preview reads the file with the standard double quote and falls back to what was detected. Sniffing the whole file was rejected: 20.84 s for 1 GB, against a 5 s budget. This leaves one limit, for malformed files only (AC-27).
- **Mixed line endings, decided 2026-11-03:** only DuckDB's lax mode reads a file whose sample mixes line endings, and it drops an extra field past the sample without saying so. Refusing these files was rejected, because valid files then can't connect. The limit is accepted (AC-27), and the lax read is only used when no other sniff can read the file.
- **Very wide files, decided 2026-11-03:** the number of columns isn't capped. At 20,000 columns and 200 rows, one page measured a 99 MB response and a 2.4 GB peak. Nobody has needed a cap yet, so this is recorded as a limit.
- **Non-CSV content, decided 2026-11-02:** files holding only a byte-order mark or blank lines, and prose or HTML, connect with whatever columns DuckDB finds. Refusing them waits for the later M2 CSV work.
- **Metadata label, decided 2026-11-02:** the details panel carries the label (AC-25). The column names in the Data Explorer are navigation and don't.
- **Deferred:** OS credential store (until M4 or M8, when something first stores a secret) and the Docker PostgreSQL fixture (M4).
- **CI:** the configuration is written now. It stays NOT RUN until the repo has a remote, and the same checks run locally.

Decided earlier, and binding here:
- ADR 0001: `drivnx [WORKSPACE_DIR] [--port N] [--no-browser]`, distribution `drivnx-data`, one workspace per runtime, `~/DrivnX/default` by default, launch code and `HttpOnly` cookie handshake.
- ADR 0002: SQLite only, one transaction per domain operation, append-only history, DuckDB spill files inside the workspace.
- ADR 0003: DrivnX-native rules. A ContractRule can be Inferred, Confirmed or Defined, and only Defined rules are part of the contract. `Contract` has no `state`. ODCS export is M7.

API for this slice, using the Section 12 outline as amended: `/api/session`, `/api/workspace`, `/api/sources`.

## Acceptance Criteria
- AC-01 (US-01): Given DrivnX is installed and `~/DrivnX/default` doesn't exist, when the user runs `drivnx`, then that workspace is created, a browser opens on `http://127.0.0.1:<port>/`, the app loads signed in, and the launch code is gone from the address bar.
- AC-02 (US-01): Given any directory that is new, empty, holds only `.drivnx.lock`, or is already a workspace, when the user runs `drivnx <dir>`, then DrivnX opens it as that workspace.
- AC-03 (US-01): Given a non-empty directory that isn't a workspace, when the user runs `drivnx <dir>`, then DrivnX exits with a message suggesting a subdirectory, adds no file, and leaves the directory's permissions unchanged.
- AC-04 (US-01): Given a runtime has a workspace open, when a second runtime starts on it, then the second exits with a clear message. Given the first was killed with `SIGKILL`, when a new runtime starts, then it opens the workspace normally.
- AC-05 (US-01): Given `--no-browser`, or a browser that fails to open, when DrivnX starts, then it prints the launch URL to the terminal. Given `--port N`, then it listens on that port, and otherwise the OS picks a free port.
- AC-06 (US-01): Given the runtime is running, when the user presses Ctrl+C, then it stops, and the previous session no longer works after a restart.
- AC-07 (US-02): Given any bind address other than a loopback address, when the runtime tries to start on it, then it refuses.
- AC-08 (US-02): Given a call to any `/api/*` route except `/api/session` without a valid session cookie, then the response is 401.
- AC-09 (US-02): Given a call to any `/api/*` route, including `/api/session`, with a wrong Host, or a wrong Origin, or no Origin on a state-changing method, then the response is 403.
- AC-10 (US-02): Given a launch code, when it is used a second time or more than 2 minutes after launch, then it is rejected.
- AC-11 (US-02): Given a successful handshake, then the session cookie is `HttpOnly` and `SameSite=Strict` with `Path=/api`, and the token is in no URL or browser storage.
- AC-12 (US-02): Given a full run of this slice's journey with logging at its most detailed, then the logs contain no session token, launch code or preview row values.
- AC-13 (US-02): Given every GET and HEAD route under `/api`, when each is called, then no workspace data changes.
- AC-14 (US-03): Given a newly created workspace, when the app first loads, then it shows the workspace's location and states that no credentials are stored in this slice, and asks for a name and an AI mode (off, external or local), with off selected.
- AC-15 (US-03): Given setup is complete, when the runtime is restarted on the same directory, then the workspace's name, AI mode and sources are as they were, and setup isn't shown again.
- AC-16 (US-03): Given any AI mode is selected, then no AI request is made, because AI arrives in M8.
- AC-17 (US-03): Given a new workspace on Linux or macOS, then the directory has mode 0700 and `workspace.db` has mode 0600.
- AC-18 (US-03): Given a workspace path under a known sync folder, when DrivnX opens it, then it warns the user.
- AC-19 (US-03): Given a workspace whose schema is older than the code, when it is opened, then a backup with mode 0600 is made and it migrates. Given a schema newer than the code, then opening is refused and the file is unchanged.
- AC-20 (US-04): Given the path of a readable CSV, when the user connects it, then it appears as a source, and the file's contents and modified time are unchanged and no copy of it exists in the workspace.
- AC-21 (US-04): Given a path that doesn't exist, is a directory, can't be read, isn't parseable as CSV, is UTF-16 without a BOM, is a Windows-1252 or big-endian UTF-16 file over 100 MB, or is a little-endian UTF-16 file over 100 MB with characters above U+FFFF such as emoji, when the user connects it, then a specific error says which, and no source is added. The over-limit error says to save the file as UTF-8.
- AC-22 (US-04): Given each synthetic CSV covering the assumed delimiters, encodings and header cases, when it is connected, then the detected delimiter, encoding and header setting are correct and are shown to the user. Given a file where detection can't tell a header from data, one row from the file may be used as column names, and the delimiter may be wrong. The screen shows the detected delimiter and says a row from the file was used as column names. Correcting either comes later in M2.
- AC-23 (US-04): Given a source whose file has since moved or been deleted, when the workspace is reopened, then the source is listed as unavailable and the app keeps working.
- AC-24 (US-05): Given a connected CSV, when its source is opened, then the Data Explorer shows it with its columns, and the app shows file name, size, modified date, and each column's name, detected type and original type text.
- AC-25 (US-05): Given a connected CSV, then no row count is shown, and every value from the scan is labelled as metadata.
- AC-26 (US-05): Given a connected CSV, then its Source, Dataset and Fields are in `workspace.db`, and a scan of the database finds no value from any data row. The one exception is the row used as column names (AC-22). No other row's values are stored.
- AC-27 (US-06): Given a connected CSV, when the preview opens, then the first 50 rows appear, and next and previous show the adjacent pages, with no total page count. Next is disabled on the last page. Given lines above the table that detection skipped, the preview says how many aren't shown. Correcting this comes later in M2. Given a malformed file whose first double quote comes after the rows detection samples and never closes, the rows after that quote may not appear. Given a file with mixed line endings near its start, a field beyond the expected columns in a later row may not be shown.
- AC-28 (US-06): Given a request for more than 200 rows per page, then the API refuses or caps it to 200.
- AC-29 (US-06): Given preview cells containing HTML or script text, then they are displayed as plain text and nothing runs. Given a cell or column name longer than 1,000 characters, then it is cut and visibly marked.
- AC-30 (US-06): Given preview pages have been viewed, then no row values are in `workspace.db` or its backups, the browser's disk cache, or any files remaining in `duckdb-tmp/` or the current directory after the job finishes.
- AC-31 (US-06): Given a file changes or disappears between pages, when the next page is requested, then the user gets current data or a clear error, and never a crash.
- AC-32 (US-07): Given the app is open, then it shows a header with workspace name, profile status, AI mode, privacy indicator and export, a left Data Explorer, centre tabs (Overview, Profile, Dictionary, Contract) and a right Intelligence & Review panel.
- AC-33 (US-07): Given anything not yet built (profile status, export, the tabs and the right panel), then it is visibly marked as not available yet and has no working action.
- AC-34 (US-07): Given AI mode is off, then the privacy indicator reads "Processing: Local · External AI: Off". Given another mode is selected, then the indicator shows that mode.
- AC-35 (US-08): Given the M1 entities in Section 12, then each one can be created and read back from SQLite through the artifact service, with everything it was saved with. A DictionaryEntry also holds its assumptions (Section 3).
- AC-36 (US-08): Given each allowed transition in Section 12, as amended by ADR 0003, then it succeeds and records a ProvenanceEvent in the same transaction. This covers New → Observed, Inferred or Confirmed; Inferred → Confirmed; Confirmed → Defined; edits; and both demotions. A Finding is New → Inferred, written by a heuristic or AI (Section 5), and records its event the same way.
- AC-37 (US-08): Given an AI or system actor, when it tries Inferred → Confirmed or Defined, then the domain layer rejects it and nothing is written.
- AC-38 (US-08): Given a user rejects or defers an item, then it stays Inferred and leaves the review inbox, the outcome is in provenance, and a deferred item can return to the inbox.
- AC-39 (US-08): Given a newer completed profile run contradicts an item's evidence, then its state is unchanged and it is flagged "needs re-review" until a user reviews it again. The system records the flag as a ProvenanceEvent, for a completed run that ended after the item's latest event. A user who still agrees with a Confirmed or Defined item clears it with Keep as is, which changes nothing else and records its event.
- AC-40 (US-08): Given a failure halfway through a transition, then neither the state change nor its ProvenanceEvent is saved.
- AC-41 (US-08): Given two concurrent Accepts on one item, then one succeeds and the other is rejected by the transition check, and neither gets `SQLITE_BUSY`. Given a profile run writing observations, then a review action finishes within `busy_timeout`.
- AC-42 (US-08): Given the artifact service's full test suite, then it never updates or deletes observations, revisions or provenance rows, and a ProfileRun changes only its status and end time, once.
- AC-43 (US-08): Given a run left `running` when the runtime stopped, when the next runtime starts, then it is marked `interrupted`, and findings ignore it.
- AC-44 (US-08): Given a batch migration of a table with referencing rows, then those rows survive, and a foreign-key violation rolls the migration back.
- AC-45 (US-08): Given a Defined rule, when it is demoted, then a Confirmed revision is written, the rule leaves the contract, a new contract version is created, and its history and provenance stay intact. Given a rule is deleted, then a tombstone revision is written and no rows are removed.
- AC-46 (US-08): Given a slot already held by a Defined rule (ADR 0003 point 4), when a second rule is promoted into it, then it is rejected and nothing is written. Overlapping candidates are allowed, and changing a candidate creates no contract version.
- AC-47 (US-08): Given a rule written directly, then it goes New → Confirmed → Defined in one transaction with both events recorded.
- AC-48 (US-08): Given any completed test run of this slice, then a scan of `workspace.db` finds no credential-shaped value.
- AC-49 (US-08): Given a DuckDB job, then it creates nothing in the current directory, its `temp_directory` is `<workspace>/duckdb-tmp/`, and leftovers there are removed at the next start.
- AC-50 (US-09): Given a clean checkout, when dependencies are installed from the committed `uv` and `pnpm` lockfiles with frozen settings, then installation succeeds without changing a lockfile.
- AC-51 (US-09): Given a built wheel, when it is installed with `uv tool install`, then the `drivnx` command serves the bundled UI and API from one origin, and AC-01 passes against it.
- AC-52 (US-09): Given the Vite dev server, then the browser uses the same cookie flow through its proxy, and the dev origin is allowed only with the dev flag.
- AC-53 (US-09): Given the repo, then CI configuration runs pytest, Vitest, Playwright, secret scanning and dependency vulnerability checks. Each of those also runs locally with a documented command.
- AC-54 (US-09): Given `testdata/`, then every file is synthetic and generated by a committed script or documented as hand-written.
- AC-55 (US-08): Given a field with a Confirmed dictionary entry, when a second entry for that field would become Confirmed, then it is rejected and nothing is written. Inferred candidates for the field are allowed, and demoting the Confirmed entry frees the field.

## Non-functional
- **Security:** AC-07 to AC-13. Telemetry is off, and nothing leaves the machine in this slice.
- **Performance (ASSUMPTION, confirm in spike 1):** on a 1 GB synthetic UTF-8 or Latin-1 CSV on a development laptop, connecting and showing the metadata takes at most 5 seconds and the first preview page at most 2 seconds. Later pages may be slower, and the measured figures are recorded.
- **Accessibility (ASSUMPTION):** built screens meet WCAG 2.2 AA, can be used by keyboard alone, and pass an automated check in Playwright.
- **Compatibility:** Python 3.12, and current Chrome, Firefox, Edge and Safari. Only what can be run here is claimed as tested.

## Verification
- Each criterion is covered by a test that names its ID, such as `CFX-APP-first-slice/AC-01`.
- pytest for domain, store and API; Vitest for components; Playwright for the journey in AC-01, AC-14, AC-20, AC-24 and AC-27 against the installed wheel.
- Spike 6 (local browser security) uses AC-07 to AC-13, plus a manual check of accepted risk R1 in the threat register.
- macOS, Windows and CI are reported as NOT RUN until they can run.

## Dependencies
- ADRs 0001, 0002 and 0003 (all Accepted).
- Decision still needed before publishing, not before building: reserving `drivnx-data` on PyPI.
- A remote for this repo, needed only to run CI. The user has parked this for now.

## Out of Scope
Later in M2: XLSX and Parquet, a file browser, correcting detection and column types, Recent and Pinned tables.
Later milestones: profiling, row counts and findings (M3), PostgreSQL and credentials (M4), overview (M5), dictionary screens (M6), contract editor and ODCS export (M7), AI (M8), and hardening (M9).
Not planned for Phase 1: anything in the Section 1 scope boundary's right-hand column.

## Open Questions
None blocks approval.
- **What counts as a run contradicting an item (AC-39)?** Due with the profiling work in M3. It needs measurements and rule types that don't exist in this slice, so this slice builds the flag and the way to clear it, and nothing raises the flag yet. When it's built, write a run's flags in the same transaction that finishes it, or compare against when detection began: any event between a run ending and its flag gets that flag refused, and the contradiction is lost (found reviewing T08).
- **What is `packages/contracts` for?** Left undefined in ADR 0003. To settle if this slice needs the folder, otherwise it stays empty.
- **Rule for values copied from source data.** Due before M3 (R3 in the threat register). Only metadata is saved here, apart from the row used as column names (AC-26).

## Conflicts with Current System
- **Section 12, "Secrets: OS credential store from day one":** deferred by the user's decision, 2026-11-01. The rule that secrets never go in SQLite, URLs, browser storage or logs still applies, and AC-48 tests it. Section 12 now carries a note saying so.
- **Section 12, day-one checklist:** the Docker PostgreSQL fixture is deferred by the same decision. The "ADR template" item is not done, and the existing ADRs serve as the pattern.
- **Section 12, Finding and DictionaryEntry:** by the user's decisions on 2026-11-04, a Finding records an author and event as Inferred, as Section 5's table labels it. A DictionaryEntry adds "assumptions", which Section 3 lists, and a field has at most one Confirmed entry. Section 12 now carries a note saying so.
- **Section 12, trust-state rules:** by the user's decision on 2026-11-04, "needs re-review" is a ProvenanceEvent, and a user clears it on an item they still agree with through a new Keep as is action. Section 12 lists neither, and now carries a note saying so.
- **Section 5, CSV "user can correct inferred types":** deferred to later in M2, not dropped. This also covers correcting the header setting and the delimiter.

## Revision History
- Revision 1: Initial draft from the user's decisions on 2026-11-01. Approved by the user in chat, 2026-11-01.
- Revision 2: Testing DuckDB in T05 changed what detection can promise. Adds Windows-1252 and both UTF-16 byte orders, with a 100 MB limit where the file has to be decoded in memory; limits AC-22 and AC-26 for all-text files without a header; limits the performance target to UTF-8 and Latin-1. Approved by the user in chat, 2026-11-02.
- Revision 3: Verifying T05 found that detection misreads files without a header that have commas in every row. Limits AC-22 and AC-26 for those files, and records that non-CSV content and the Data Explorer's column names stay as built. Approved by the user in chat, 2026-11-02.
- Revision 4: Reviewing T05 found that a row can become the column names in more files than revisions 2 and 3 named, including files with a real header, and that no detection rule can prevent it. Replaces those limits on AC-22 and AC-26 with one general limit. Adds little-endian UTF-16 files with characters above U+FFFF to the 100 MB limit in AC-21. Approved by the user in chat, 2026-11-03.
- Revision 5: Verifying T06 found that the preview can silently leave out real rows above the table, and that the browser saved row values in its disk cache. AC-27 now says how many lines aren't shown, and AC-30 now covers the browser's disk cache. Approved by the user in chat, 2026-11-03.
- Revision 6: Reviewing T06 found that quoting is decided from the start of a file and that column names weren't cut. AC-27 records the limit that remains for malformed files, and AC-29 now covers column names. Records that very wide files have no cap. Approved by the user in chat, 2026-11-03.
- Revision 7: Fixing T06 found that reading mixed line endings can drop an extra field in a later row. AC-27 records this limit. Approved by the user in chat, 2026-11-03.
- Revision 8: Reviewing T07 raised three questions about the domain model. A Finding records its author and event as Inferred, a DictionaryEntry holds assumptions, and a field has at most one Confirmed entry (new AC-55). AC-35 now says everything saved reads back. Approved by the user in chat, 2026-11-04.
- Revision 9: Building T08 settled how "needs re-review" works (AC-39). The system records it as a ProvenanceEvent, a new Keep as is action lets a user clear it without changing the item, and what counts as a contradiction waits for M3's profiling work. Approved by the user in chat, 2026-11-04.
