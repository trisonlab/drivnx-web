---
id: CFX-APP-csv-profile
kind: feature
title: "Profile a CSV: correct how it's read, profile it, and see findings with evidence"
summary: "Correct CSV detection, run a fast or full deterministic profile, read labelled metrics in the Profile tab, and see findings backed by evidence, with only aggregates saved."
owner: conflux-web
lifecycle: approved
revision: 2
approved_revision: 2
approval_evidence: "user approved revision 2 in chat, 2026-11-06 (chose 'Revise spec first' with the AC-20 wording, on delivering T05)"
updated: "2026-11-06"
related: [CFX-APP-first-slice]
status: CONFIRMED
---

# Profile a CSV: correct how it's read, profile it, and see findings with evidence

## Problem
A user can connect a CSV and page through it, but Conflux doesn't yet tell them anything about their data. This slice delivers M3 for CSV files, so a user gets the first real evidence and findings. Detection can be wrong and observations can't be edited, so the slice also lets users correct how a file is read, which stops wrong evidence being saved.

Source of truth: `../docs/Conflux_Phase1_Development_Direction.md` (Sections 3, 5, 7, 8 and 10), ADRs 0001, 0002 and 0003 in `docs/adr/`, and the first slice's spec `CFX-APP-first-slice`.

## Scope
In:
- Correcting a CSV's header setting, delimiter, encoding and skipped lines.
- A fast profile and a full profile, with cancellation.
- The Profile tab, the header's profile status, and the findings section of the right panel.
- Six kinds of rule-based finding, each linked to its evidence.
- Rule-based detection of possibly sensitive fields.
- Values computed on request and never saved (the R3 rule).
- Earlier runs, kept and labelled.

## Assumptions
- ASSUMPTION: a profile starts from a button in the Profile tab, not automatically after connecting, because the user journey has the user choose the depth (Section 2 of the direction doc).
- ASSUMPTION: one profile run at a time per runtime. Starting a second is refused with a clear message.
- ASSUMPTION: the delimiters and encodings a user can pick are those the first slice detects: comma, semicolon, tab and pipe; UTF-8, UTF-16, Latin-1 and Windows-1252. The existing 100 MB limit still applies to files that must be decoded in memory.
- ASSUMPTION: lines to skip is a whole number from 0 to 1,000.
- ASSUMPTION: a correction is applied when saved. There's no separate trial preview before saving.
- ASSUMPTION: "missing" means no value, or a value holding only whitespace.
- ASSUMPTION: "possible identifier" means at least 99% unique among non-missing values, and at least one non-missing value. The direction doc's example is 99.8%.
- ASSUMPTION: "significant missing values" means at least 20% missing.
- ASSUMPTION: masked patterns keep the 10 most common per column, and a pattern is cut at 40 characters and marked as cut.
- ASSUMPTION: findings are listed in a fixed order by kind: possibly sensitive, values that don't fit the type, duplicate rows, significant missing values, possible identifier, single-value column. Real prioritisation is M5.
- ASSUMPTION: values computed on request use the same part of the file as the latest completed run: the first 100,000 rows after a fast profile, or the whole file after a full one.
- ASSUMPTION: the sensitive-field rules cover names and patterns for email, phone, payment card, national or tax ID, date of birth, postal address and personal name. Patterns are only checked for email, phone and payment card, as the others have no reliable shape.
- ASSUMPTION: the performance targets under Non-functional are starting targets, and the time for an all-text read and the duplicate-row check is not yet measured.
- ASSUMPTION: the built screens meet WCAG 2.2 AA.

## User Stories
### US-01 — Correct how a file is read when detection got it wrong
### US-02 — Run a fast or full profile, and cancel it
### US-03 — Read each metric and know exactly what it was measured on
### US-04 — See findings and check the evidence behind each one
### US-05 — See which fields may be sensitive, without believing the list is complete
### US-06 — See real values when I ask, without Conflux saving them
### US-07 — Go back to earlier runs and know when results are out of date
### US-08 — As a developer, prove the metrics, the storage rule and the targets

## Decisions and Contracts
Decided with the user in chat, 2026-11-05:
- **Order:** this slice comes before the rest of M2 (XLSX, Parquet, file browser, Recent and Pinned tables), which changes Section 10's M2-then-M3 order. Only detection correction moves forward.
- **The R3 rule:** only aggregates are saved: counts, percentages, text lengths and masked patterns (letters become `A`, digits become `9`). Min, max, frequent values, histograms, mean, median and standard deviation are computed on request and never saved. Histograms are included because their bucket edges reveal real values, and mean and median because they can be, or reveal, a real value.
- **What follows from the R3 rule:** values that aren't saved aren't observations, so findings can't cite them. A finding can still use a count, such as how many values don't fit the type. An on-request result needs the file to still be readable, and can differ from the run if the file has changed.
- **Fast profile:** reads the first 100,000 rows, labelled "first rows, not random". Random sampling was rejected: measured on 2026-11-05, 100,000 random rows took 5.75 s against 0.12 s for the first rows and 3.86 s for exact whole-file counts, because a random sample of a CSV still reads the whole file. One run each, 1 GB, 5 columns, on a RAM disk.
- **Full profile:** reads the whole file and gives exact results, only on explicit request (Section 5).
- **Masked patterns:** always come from the first 100,000 rows, even on a full profile, and are labelled that way. On the whole file they took about 8 s per text column. A rare pattern that first appears later is missed.
- **Corrections:** header, delimiter, encoding and skipped lines. Column types move to the "other local sources" slice.
- **Earlier runs after a correction:** they're kept, labelled "used earlier settings", and their findings stop being current.
- **Column names replaced after a correction:** a row wrongly used as column names may hold personal data, so previous names are replaced with generated ones in the saved data. To keep the rule simple, this applies to any correction that changes the names, so an earlier run may lose good labels too.
- **Findings:** the six kinds in the Assumptions. Duplicate rows appear on a full profile only. Findings from a fast profile say "in the first 100,000 rows".
- **Relationships:** candidate relationships move to the executive overview slice, with spike 3.
- **Sensitive fields:** rule-based (Section 9 of the direction doc), marked Inferred, with an extra confirmation before showing values from a flagged column.
- **What counts as a contradiction (AC-39 of the first slice):** an item is flagged for re-review when a newer completed run shows that a column it cites has gone or changed type, or that a percentage it cites moved by more than 5 points. A sample run can't contradict evidence from a full run. This definition is agreed now and built in the dictionary slice, because no screen can confirm an item until then. The flags must be written in the transaction that finishes the run, as the first slice's spec records.

Binding from earlier:
- ADR 0002: observations are written once and never changed, in transactions of at most 1,000, and a run's status changes only once. DuckDB spill files stay inside the workspace.
- First slice: findings are Inferred and nothing reviews them further. Runs left `running` become `interrupted` at the next start, and findings ignore them.

API, following the Section 12 outline: `/api/profile-runs` to start, read status, cancel and read results; `/api/findings`; a route under `/api/sources` to change settings; and a GET route for on-request values.

## High-Level Design
- `packages/profiling` (empty today) computes metrics through DuckDB, without knowing about CSV details, so that Parquet can use it later.
- `packages/connectors` supplies the read settings for a source.
- `packages/artifacts` saves runs, observations and findings through the functions that already exist.
- Runs work in the background, and the browser asks for their status.

## Low-Level Design
- A column's data is read as text, so a value that doesn't fit the type can be counted instead of failing the read.
- A correction adds a new dataset and fields under the source, so older observations still point at what they measured. `field` has `UNIQUE (dataset_id, position)` and a correction can change the number of columns.
- A run's `scope` records the dataset, the read settings, and the file's size and modified time.
- A finding's saved text holds no column name or source value. The column comes from the observation it cites, so a name replaced later can't survive in a finding.
- Replacing column names uses `PRAGMA secure_delete` and a WAL checkpoint, as ADR 0002 suggests.

## Acceptance Criteria
- AC-01 (US-01): Given a connected CSV, when the user opens its settings, then the current header setting, delimiter, encoding and lines to skip are shown and can be changed.
- AC-02 (US-01): Given a file read with the wrong delimiter, header setting, encoding or skipped lines, when the user saves the right value, then the columns and the preview show the file read that way, and restarting keeps it.
- AC-03 (US-01): Given settings that can't read the file, or an encoding over the 100 MB limit, when the user saves, then they get a clear message and the previous settings stay in use.
- AC-04 (US-01): Given a correction that changes the column names, then every earlier name for that source is replaced with a generated name, and a raw scan of `workspace.db` and its WAL file no longer finds the old names. Pre-migration backups are a known limit.
- AC-05 (US-01): Given a path already connected with corrected settings, when it is connected again, then the existing source opens with the corrected settings.
- AC-06 (US-01): Given a profile run in progress, when the user tries to save a correction, then it is refused with a clear message.
- AC-07 (US-02): Given a connected CSV and no run in progress, when the user chooses Fast profile, then a run reads at most the first 100,000 rows and completes with its results shown.
- AC-08 (US-02): Given a connected CSV, when the user chooses Full profile, then a run reads every row and completes with exact results and the file's row count.
- AC-09 (US-02): Given a run in progress, then the header's profile status and the Profile tab show that it's running, and a second run is refused with a clear message.
- AC-10 (US-02): Given a run in progress, when the user cancels it, then it stops within the target under Non-functional, its status is `cancelled`, and no results or findings from it appear as current.
- AC-11 (US-02): Given a file that has moved, was deleted or can't be read, when a run starts or fails partway, then the user gets a clear message, a run that started ends as `failed`, and its results don't appear as current.
- AC-12 (US-02): Given a file whose columns no longer match the saved ones, when a run starts, then it is refused with a message pointing to the source's settings, and saving those settings reads the columns again.
- AC-13 (US-02): Given a profile job, then it creates nothing in the current directory, and any spill files are inside `<workspace>/duckdb-tmp/`.
- AC-14 (US-03): Given a completed run, then for each column the Profile tab shows row count read, missing count and percentage, distinct count and percentage, values not fitting the type, shortest, longest and average text length, and the most common masked patterns with counts. Numeric and date columns show the same set.
- AC-15 (US-03): Given any metric, then the screen shows its scope (whole file or first rows), its precision (exact or approximate) and its run's time and ID, and results from different scopes are never mixed without a label.
- AC-16 (US-03): Given a fast profile of a file with more than 100,000 rows, then its metrics are labelled "first 100,000 rows, not random", and no total row count is claimed.
- AC-17 (US-03): Given a fast profile of a file with at most 100,000 rows, then its metrics are labelled as the whole file.
- AC-18 (US-03): Given a full profile, then its masked patterns are labelled as coming from the first 100,000 rows, and its other metrics as the whole file.
- AC-19 (US-03): Given each synthetic golden file, covering missing values, duplicates, dates, Unicode, mixed types and high cardinality, when it is profiled, then every saved metric equals its known expected value.
- AC-20 (US-03): Given Unicode text, then letters in any script become `A`, together with any combining marks after them, and digits and other numbers in any script, such as `½` and `Ⅷ`, become `9`. Punctuation, symbols, spaces and a mark with no letter before it stay. A character too new for DuckDB's Unicode tables becomes `A`. Given a value longer than 40 characters, then its pattern is cut and marked as cut.
- AC-21 (US-04): Given a run that meets a finding's condition, then a finding of that kind appears for each column or dataset concerned: possible identifier, significant missing values, single-value column, values that don't fit the type, or possibly sensitive field. A run that doesn't meet a condition creates no finding of that kind.
- AC-22 (US-04): Given a full profile of a file with fully duplicated rows, then a duplicate-rows finding appears with the count. Given a fast profile, then no duplicate-rows finding appears, and the screen says the check needs a full profile.
- AC-23 (US-04): Given a finding, then it shows what was observed, what's inferred, why it matters, the recommended action, and its Inferred state, and its evidence link opens the cited metric with its run.
- AC-24 (US-04): Given a finding from a fast profile, then its text says "in the first 100,000 rows".
- AC-25 (US-04): Given a saved finding, then its saved text holds no column name and no value from the file, and it cites at least one observation.
- AC-26 (US-04): Given a run that was cancelled, failed or interrupted, then it creates no findings and earlier findings stay as they were.
- AC-27 (US-05): Given synthetic columns matching the name rules or the email, phone or payment card patterns, then each gets a possibly-sensitive finding saying whether a name or a pattern matched. No AI is involved.
- AC-28 (US-05): Given findings for possibly sensitive fields, or none, then the screen says that having no flag doesn't mean a column has no personal data.
- AC-29 (US-05): Given a column flagged as possibly sensitive, when on-request values are requested without the extra confirmation, then the API refuses and shows no values. With the confirmation, they are shown.
- AC-30 (US-06): Given a column in a completed run, when the user asks to see its values, then min, max and the 10 most frequent values are shown, and for numeric and date columns also a histogram, and mean, median and standard deviation where they apply. They are labelled with the part of the file they came from and the time they were computed.
- AC-31 (US-06): Given values shown on request, then each is cut at 1,000 characters and marked when cut, as in the preview, and the response is `Cache-Control: no-store`.
- AC-32 (US-06): Given a file that is no longer readable, when values are requested, then the user gets a clear message, and the saved metrics still show.
- AC-33 (US-06): Given a full journey with distinctive planted values in a synthetic file, including on-request values, then a raw scan of `workspace.db` and its WAL finds no value from the file, except masked patterns and column names, and logs at their most detailed hold none either.
- AC-34 (US-06): Given every new GET route, when it is called, then no workspace data changes, and it follows AC-08, AC-09 and AC-13 of the first slice.
- AC-35 (US-07): Given several runs of a source, then the user can list them with mode, status and time, open any completed one, and the newest completed run with the current settings is shown by default.
- AC-36 (US-07): Given a run made before a correction, then it is labelled "used earlier settings", and its findings no longer appear as current.
- AC-37 (US-07): Given a file whose size or modified time differs from the latest run's, then the Profile tab says the results may be out of date.
- AC-38 (US-07): Given a restart, then completed runs, their metrics and findings are as they were, and a run that was in progress shows as `interrupted`.
- AC-39 (US-08): Given a file with more columns than fit in one transaction's limit, when it is profiled, then all its observations are saved, in transactions of at most 1,000, and a review action finishes within `busy_timeout` during the run.
- AC-40 (US-08): Given `testdata/`, then each new file is synthetic and generated by a committed script or documented as hand-written.
- AC-41 (US-08): Given the installed wheel, then the journey of connecting a CSV, correcting it, running a fast profile, running a full profile and opening a finding's evidence passes in Playwright from one origin, with an automated accessibility check on the new screens in each state.
- AC-42 (US-08): Given the threat register and the direction doc, then R3 records the rule for profiling and what stays open, and an ADR records the rule and the profile-caching decision.

## Non-functional
- **Performance (ASSUMPTION, starting targets):** on a 1 GB, 5-column synthetic UTF-8 or Latin-1 CSV on a development laptop's disk, a fast profile takes at most 5 s, a full profile at most 60 s, and cancelling stops a run within 5 s. Figures are measured and recorded, as in the first slice. Files with many columns take longer, and the number of columns still isn't capped, which is recorded as a limit.
- **Security:** no value from a file is saved except as allowed by the R3 rule. Nothing leaves the machine. New routes keep the session, Host and Origin checks.
- **Accessibility (ASSUMPTION):** new screens meet WCAG 2.2 AA, work by keyboard alone, and don't rely on colour alone for scope, precision or trust state.
- **Compatibility:** as in the first slice. Only what can be run here is claimed as tested.

## Verification
- Each criterion is covered by a test that names its ID, such as `CFX-APP-csv-profile/AC-01`, except that AC-42 is checked by reading the documents.
- pytest for profiling, storage and the API; Vitest for components; Playwright for AC-41 against the installed wheel.
- The first ticket measures an all-text read and the duplicate-row check on 1 GB, to confirm the targets before the rest is built.
- macOS, Windows and CI are reported as NOT RUN until they can run.

## Dependencies
- `CFX-APP-first-slice`, which is finished but not merged. By the user's decision on 2026-11-05, tickets branch from `feat/cfx-app-first-slice-t10-full-journey-and-project-checks`.
- ADRs 0001, 0002 and 0003.

## Out of Scope
Later in M2: XLSX and Parquet, a file browser, correcting column types, Recent and Pinned tables, and refusing non-CSV content.
Later milestones: candidate relationships and the overview (M5), dictionary screens and raising "needs re-review" (M6), the contract editor (M7), AI (M8), exporting a profile (M7 and M9).
Not planned for Phase 1: anything in the Section 1 scope boundary's right-hand column.

## Open Questions
None blocks approval.
- **Values in dictionary examples and contract accepted values (R3).** This rule covers profiling only. Those values are typed or chosen by a user, and the rule for them is due with the dictionary and contract slices.
- **A row used as column names that the user never corrects.** It stays saved, and the screen says a row from the file was used (first slice, AC-22 and AC-26). This is the one known exception to the R3 rule, and it's recorded as a limit.

## Conflicts with Current System
- **Section 10, milestone order:** this slice does M3 for CSV before finishing M2, by the user's decision on 2026-11-05. Section 10 needs a note saying so.
- **Section 5, Fast profile "sample-based":** first rows aren't a random sample. The label says so, and Section 5 needs a note.
- **Section 5, CSV "user can correct inferred types":** still deferred, now to the "other local sources" slice.
- **Section 5, Level 2 candidate relationships:** moved to the executive overview slice.
- **Section 5, Level 2 and Section 12, metrics with values:** min, max, frequent values and distributions aren't saved as observations (R3 rule), which changes what ADR 0002's "observations are always stored" covers: these aren't observations. The rule needs a note in Section 7, as ADR 0002 asks.
- **First slice AC-33:** says profile status, the Profile tab and the right panel are marked as not available. This slice builds the status, the tab and the panel's findings section, so the tests for AC-33 must change for those parts. The other tabs and panel sections stay marked.
- **First slice AC-22 and AC-27 text:** "correcting comes later in M2" is delivered here.
- **First slice AC-25:** says no row count is shown. That stays true until a full profile, and AC-08 here adds the count in the Profile tab.
- **`finding` table:** has no field saying what kind a finding is, and AC-21 and the fixed order need one, so a migration is likely.

## Revision History
- Revision 1: Initial draft from the user's decisions on 2026-11-05. Approved by the user in chat, 2026-11-05.
- Revision 2: AC-20 matches decision 1 of ADR 0004, found while building T05. No change to the behaviour agreed in T05's plan. Approved by the user in chat, 2026-11-06.
