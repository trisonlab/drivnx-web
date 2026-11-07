---
id: CFX-APP-csv-profile-T01
title: "Measure and record the rule"
feature: CFX-APP-csv-profile
feature_revision: 4
stories: [US-08]
criteria: [AC-42]
blocked_by: []
state: done
external_key: null
status: CONFIRMED
---

# Measure and record the rule

## Outcome
The all-text read and the duplicate-row check are timed on a 1 GB CSV, so the targets are confirmed or a spec change is raised before any profiling is built. The R3 rule and the profile-caching decision are written down where the threat register and the direction doc expect them.

## Acceptance checklist
- [x] AC-42 (US-08): Given the threat register and the direction doc, then R3 records the rule for profiling and what stays open, and an ADR records the rule and the profile-caching decision.

## Expected evidence
Measured figures in this ticket, on a real disk. The new ADR, the updated R3 row, and notes in Sections 5, 7 and 10 of the direction doc. AC-42 is checked by reading the documents.

## From planning
- If a timing misses a target, stop and raise a spec change before T04.

## Measured
On 2026-11-05, on this machine's NVMe disk, with the page cache dropped first and DuckDB limited to 4 GB. These are the queries alone, read as all text, with nothing saved and no UI, on 1 GB, 5-column files. The script was throwaway. A full profile here means the metrics, the duplicate-row check and masked patterns from the first 100,000 rows.

| File | Run | Fast profile | Full profile | Highest peak memory in any job | Cancel stopped after |
|---|---|---|---|---|---|
| Typical, UTF-8 | 1 | 1.00 s | 16.70 s | 3.00 GB | 0.01 s |
| Typical, UTF-8 | 2 | 0.74 s | 17.13 s | 3.06 GB | 0.00 s |
| Typical, Latin-1 | 1 | 1.19 s | 20.47 s | 2.92 GB | 0.00 s |
| Typical, Latin-1 | 2 | 1.21 s | 19.29 s | 3.07 GB | 0.00 s |
| All unique text, UTF-8 | 1 | 2.53 s | Not run together: metrics 6.64 s, duplicates 1.83 s | 2.28 GB | 0.04 s |

The duplicate-row check alone took 3.91 to 5.13 s on the typical files. Every figure is within the targets of 5 s, 60 s and 5 s, so the spec doesn't change and T04 can start once T02 is done.

These don't prove the product will meet the targets. Saving observations, Python and the UI aren't included, and T12 measures those. Cancelling was only tried 1.5 s into reading the file, so T06 should try it during the duplicate-row check too. This is one machine with 8 cores, and a slower laptop will take longer.
