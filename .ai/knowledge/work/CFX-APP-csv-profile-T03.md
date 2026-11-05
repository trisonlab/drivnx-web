---
id: CFX-APP-csv-profile-T03
title: "Replace earlier column names"
feature: CFX-APP-csv-profile
feature_revision: 1
stories: [US-01]
criteria: [AC-04]
blocked_by: [CFX-APP-csv-profile-T02]
state: blocked
external_key: null
status: CONFIRMED
---

# Replace earlier column names

## Outcome
After a correction that changes the column names, the earlier names are gone from the saved data, so a row wrongly used as column names doesn't stay in the workspace.

## Acceptance checklist
- [ ] AC-04 (US-01): Given a correction that changes the column names, then every earlier name for that source is replaced with a generated name, and a raw scan of `workspace.db` and its WAL file no longer finds the old names. Pre-migration backups are a known limit.

## Expected evidence
A raw byte scan of `workspace.db` and its WAL file for planted names, before and after the correction. Backups are recorded as a known limit.
