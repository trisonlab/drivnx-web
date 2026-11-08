---
id: CFX-APP-csv-profile-T03
title: "Replace earlier column names"
feature: CFX-APP-csv-profile
feature_revision: 4
stories: [US-01]
criteria: [AC-04]
blocked_by: [CFX-APP-csv-profile-T02]
state: done
external_key: null
status: CONFIRMED
---

# Replace earlier column names

## Outcome
After a correction that changes the column names, the earlier names are gone from the saved data, so a row wrongly used as column names doesn't stay in the workspace.

## Acceptance checklist
- [x] AC-04 (US-01): Given a correction that changes the column names, then every earlier name for that source is replaced with a generated name, and a raw scan of `workspace.db` and its WAL file no longer finds the old names. Pre-migration backups are a known limit.

## Expected evidence
A raw byte scan of `workspace.db` and its WAL file for planted names, before and after the correction. Backups are recorded as a known limit.

## Decisions in building this
- **Each part was shown to be needed.** The test still failed after replacing the names, and after adding `PRAGMA secure_delete`, and passed only once the WAL was emptied too.
- **`secure_delete` is on for every connection,** not only for this change, because pages get rearranged as other sources are saved, and that can leave copies behind. Its cost on a profile run is included in T12's overall figures, not measured separately.
- **Field ids stay the same,** so evidence keeps pointing at the same field. Only names change, to `column` plus the position.
- **A correction that keeps the names leaves earlier datasets alone,** so an earlier run stays readable after an encoding fix, for example.
- **Emptying the WAL waits for readers up to `busy_timeout`.** If a reader outlasts that, the save still succeeds and the old names stay in the WAL until a later checkpoint, which may not happen until the workspace closes. A one-off probe showed this: they survived another ordinary save and went when the workspace closed. Accepted by the user as a known limit of AC-04, like backups. Readers here are short (INFERRED: not every read was checked), and T07 refuses corrections during a run, so no writer competes. No committed test covers this case.

## Review of T02 and T03
- Every testdata file was tried with every encoding, both header settings, comma and tab, and automatic, 0 and 1 lines to skip: 1,740 saves gave 1,194 accepted and 546 refused, with no server errors, and every accepted one's preview read successfully and matched the saved columns. Three files can't be connected, by design. This was a one-off probe, not a committed test.
- Saving the same settings after a file changed does read the rows again. A test now holds this.

## For later tickets
- **Dictionary and contract slices:** an entry or rule points at a field in the dataset current when it was made. After a correction, it still points at the replaced dataset, whose name may now be generated. No screen can create these yet, so what should happen to them is for those slices to decide.
- **Showing earlier datasets:** in a file with 10 or more columns, the same column is `column0` in a replaced dataset and `column00` in the current one, because DuckDB pads its generated names. Nothing shows earlier datasets yet, so any screen that does will need to handle this.
- **Busy workspace, from earlier tickets:** `PUT /api/sources/{id}/settings` doesn't catch `WorkspaceBusy`, so when another program held the write lock it returned a 500 with `Internal Server Error`, not "DrivnX is busy… Try again". This isn't caused by T03 and needs its own fix. Fixed: T04's handler for the whole app answers 503 with that message, T07 added a test for it, and the settings form now shows it.
