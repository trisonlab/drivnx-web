---
id: DRIVN-63
title: "Parquet: fast and full profile, and cancel"
feature: DRIVN-60
feature_revision: 1
stories: [US-03]
criteria: [AC-12, AC-13, AC-30, AC-31]
blocked_by: [DRIVN-62]
state: ready
external_key: DRIVN-63
status: CONFIRMED
---

# Parquet: fast and full profile, and cancel

Plane: [DRIVN-63](https://app.plane.so/drivnx/browse/DRIVN-63/)

## Outcome
A user profiles a Parquet file. A fast profile reads about 100,000 rows from row groups chosen with the run's seed, so the same seed reads the same rows, reads every row of a small file, and samples within a file's one huge row group. The row count comes from the metadata. A full profile reads every row, a cancel stops a run within 5 s, and a file that moved or can't be read fails the run with its message.

## Acceptance checklist
- [ ] AC-12 (US-03): Given `events.parquet` and a run with the seed 42, when the user starts a fast profile, then it reads about 100,000 rows from row groups chosen with that seed, labelled "about 100,000 rows, sampled by row group", and the row count "40,000,000 rows" is labelled "from the file's metadata". A second fast run with the seed 42 reads the same rows. A full profile reads all 40,000,000.
- [ ] AC-13 (US-03, this ticket's part: a Parquet file; a sheet's is DRIVN-68's): Given a full profile of a 1,000,000-row sheet or a 1 GB Parquet file, when the user cancels it 3 s after it starts, then it stops within 5 s, its status is `cancelled`, and none of its results or findings appear as current.
- [ ] AC-30 (US-03): Given a Parquet file of 60,000 rows, when the user starts a fast profile, then it reads every row, labelled "every row, 60,000 rows". Given a file of 5,000,000 rows in one row group, then a fast profile still reads about 100,000 rows, sampled within that row group.
- [ ] AC-31 (US-03, this ticket's part: a Parquet file; a sheet's is DRIVN-68's): Given a sheet or a Parquet file that moved, was deleted or can't be read, when a run starts or fails partway, then the run ends `failed` with "DrivnX can't find this file. It may have moved or been deleted." or "DrivnX couldn't read this file.", and its results don't appear as current.

## Expected evidence
Integration tests: the rows a fast run reads for seed 42, twice; a 60,000-row file read whole; a 5,000,000-row, one-row-group file sampled to about 100,000; every row on a full run; a cancel 3 s into a full run of a 1 GB file, stopped within 5 s and `cancelled`; a deleted and an unreadable file ending `failed` with its message. The labels in Vitest. The performance targets for a 1 GB, 5-column file measured and recorded (fast at most 5 s, full at most 60 s).

## Notes
The ADR on how a Parquet file is sampled is written here; AC-25 checks it in DRIVN-72. The spec's open question on one huge row group is answered here.

## Blocked by
DRIVN-62.
