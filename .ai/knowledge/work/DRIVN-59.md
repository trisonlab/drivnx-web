---
id: DRIVN-59
title: "Scan a running workspace without SQLite's shared-memory index"
feature: DRIVN-9
feature_revision: 5
stories: [US-06]
criteria: [AC-33]
blocked_by: []
state: done
external_key: DRIVN-59
status: CONFIRMED
---

# Scan a running workspace without SQLite's shared-memory index

Plane: [DRIVN-59](https://app.plane.so/drivnx/browse/DRIVN-59/)

## Outcome
The AC-33 journey test scans a running workspace on Windows as reliably as on Linux and macOS, and still reads every
byte of `workspace.db` and its WAL.

## Acceptance checklist
- [x] AC-33 (US-06): Given a full journey with distinctive planted values in a synthetic file, including on-request values, then a raw scan of `workspace.db` and its WAL finds no value from the file, except masked patterns and column names, and logs at their most detailed hold none either.

## Expected evidence
The CI run on Windows for the pull request, with `test_a_journey_leaves_no_value_from_the_file_in_the_workspace_or_logs`
passing, and the same test on Linux and macOS.

## Verification
Commit `ecb7199`, 2026-11-19, CI run 44491961863 on Ubuntu, macOS and Windows.

| Criterion | Evidence | Result |
|---|---|---|
| AC-33 | `test_profile_journey.py` `test_a_journey_leaves_no_value_from_the_file_in_the_workspace_or_logs` passed on Windows (`tests\e2e\test_profile_journey.py ...`, e2e 75 passed, 5 skipped), where `main` at `891a0e8` failed it, and on Linux and macOS. The scan still reads `workspace.db` and its WAL, and finds the masked email in them, so it can't pass by reading the wrong files. | PASS |

Checks: CI's four jobs passed (`tests` on Ubuntu, macOS and Windows, `scans`); locally `pytest
tests/e2e/test_profile_journey.py` 3 passed on Linux; `detect-secrets` clean; `ai-skills knowledge check` consistent.
One Windows pass of a failure that came and went is evidence, not proof that it can't come back. NOT RUN: a
reproduction on Windows before the fix, as this machine is Linux.

## Why
`tests/e2e/test_profile_journey.py` `every_file_in` reads every file in the workspace while DrivnX runs. On Windows,
SQLite holds byte-range locks on `workspace.db-shm` while a connection is open, so the read fails with
`PermissionError` whenever one is open at that moment. CI on `main` failed this way on Windows at `891a0e8` (run
44447564878), and on DRIVN-52's pull request (run 44488203948), with the same test passing on Linux and macOS.

## Plan agreed with the user
Agreed in chat, 2026-11-19, as its own ticket before DRIVN-52 (PR #26) merges: the scan leaves out the `-shm` file, as
it leaves out the lock file. The `-shm` file is SQLite's index into the WAL, page numbers and checksums, and holds no
row of the database (INFERRED from SQLite's WAL file format). `workspace.db` and its WAL are still read, which is what
AC-33 names. No product code changes.
