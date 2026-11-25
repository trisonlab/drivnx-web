---
id: DRIVN-65
title: "Bundled excel extension and XLSX checks"
feature: DRIVN-60
feature_revision: 1
stories: [US-07]
criteria: [AC-23]
blocked_by: []
state: ready
external_key: DRIVN-65
status: CONFIRMED
---

# Bundled excel extension and XLSX checks

Plane: [DRIVN-65](https://app.plane.so/drivnx/browse/DRIVN-65/)

## Outcome
The wheel carries DuckDB's signed `excel` extension for Linux x86-64, macOS arm64 and Windows x86-64, loaded from the package with signature checks on and never downloaded, as `postgres_scanner` is. On any other platform, connecting a workbook says XLSX isn't available there, and CSV, Parquet and PostgreSQL still work. The XLSX behaviours the spec relies on are checked against the loaded extension, and the decompression limit and a large shared-strings table's memory are measured.

## Acceptance checklist
- [ ] AC-23 (US-07): Given the wheel, then it holds DuckDB's signed `excel` extension for Linux x86-64, macOS arm64 and Windows x86-64, loaded from the package with signature checks on and never downloaded, and the wheel's size with it is recorded. On another platform, connecting a workbook is refused with "XLSX isn't available on this platform (linux_arm64)." and CSV, Parquet and PostgreSQL still work.

## Expected evidence
The build hook's pinned checksums re-downloaded and checked; the extension loaded and its signature checked on each OS CI runs; a test that nothing is downloaded; the refusal on an unsupported platform with CSV, Parquet and PostgreSQL still working; the wheel's size before and after recorded. Each XLSX behaviour in the research note checked by a run and the note updated; the unpack limit and memory measured and recorded.

## Notes
A behaviour that differs from the research note goes back to DRIVN-60 as a new revision before DRIVN-66 builds on it. The ADR on the bundled extension is written here; AC-25 checks it in DRIVN-72.

## Blocked by
None, it can start now.
