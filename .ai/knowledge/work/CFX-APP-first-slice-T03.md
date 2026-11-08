---
id: CFX-APP-first-slice-T03
title: "Workspace opens safely"
feature: CFX-APP-first-slice
feature_revision: 9
stories: [US-01, US-03, US-08]
criteria: [AC-02, AC-03, AC-04, AC-17, AC-18, AC-19, AC-44]
blocked_by: [CFX-APP-first-slice-T01]
state: done
external_key: null
status: CONFIRMED
---

# Workspace opens safely

## Outcome
DrivnX decides whether it may use a directory before touching it, holds an OS lock, sets owner-only permissions, and opens SQLite with the settings in ADR 0002. Migrations back up first and can't lose referencing rows.

## Acceptance checklist
- [x] AC-02 (US-01): Given any directory that is new, empty, holds only `.drivnx.lock`, or is already a workspace, when the user runs `drivnx <dir>`, then DrivnX opens it as that workspace.
- [x] AC-03 (US-01): Given a non-empty directory that isn't a workspace, when the user runs `drivnx <dir>`, then DrivnX exits with a message suggesting a subdirectory, adds no file, and leaves the directory's permissions unchanged.
- [x] AC-04 (US-01): Given a runtime has a workspace open, when a second runtime starts on it, then the second exits with a clear message. Given the first was killed with `SIGKILL`, when a new runtime starts, then it opens the workspace normally.
- [x] AC-17 (US-03): Given a new workspace on Linux or macOS, then the directory has mode 0700 and `workspace.db` has mode 0600.
- [x] AC-18 (US-03): Given a workspace path under a known sync folder, when DrivnX opens it, then it warns the user.
- [x] AC-19 (US-03): Given a workspace whose schema is older than the code, when it is opened, then a backup with mode 0600 is made and it migrates. Given a schema newer than the code, then opening is refused and the file is unchanged.
- [x] AC-44 (US-08): Given a batch migration of a table with referencing rows, then those rows survive, and a foreign-key violation rolls the migration back.

## Expected evidence
Tests naming each criterion, including a `SIGKILL` restart and a batch migration of a table with referencing rows. Windows and macOS behaviour reported as NOT RUN.
