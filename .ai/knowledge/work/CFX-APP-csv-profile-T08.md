---
id: CFX-APP-csv-profile-T08
title: "Findings with evidence"
feature: CFX-APP-csv-profile
feature_revision: 2
stories: [US-04]
criteria: [AC-21, AC-22, AC-23, AC-24, AC-25, AC-26]
blocked_by: [CFX-APP-csv-profile-T06]
state: blocked
external_key: null
status: CONFIRMED
---

# Findings with evidence

## Outcome
Completed runs produce rule-based findings in the right panel, each showing what was observed and inferred, why it matters, what to do, and a link to its evidence.

## Acceptance checklist
- [ ] AC-21 (US-04): Given a run that meets a finding's condition, then a finding of that kind appears for each column or dataset concerned: possible identifier, significant missing values, single-value column, values that don't fit the type, or possibly sensitive field. A run that doesn't meet a condition creates no finding of that kind.
- [ ] AC-22 (US-04): Given a full profile of a file with fully duplicated rows, then a duplicate-rows finding appears with the count. Given a fast profile, then no duplicate-rows finding appears, and the screen says the check needs a full profile.
- [ ] AC-23 (US-04): Given a finding, then it shows what was observed, what's inferred, why it matters, the recommended action, and its Inferred state, and its evidence link opens the cited metric with its run.
- [ ] AC-24 (US-04): Given a finding from a fast profile, then its text says "in the first 100,000 rows".
- [ ] AC-25 (US-04): Given a saved finding, then its saved text holds no column name and no value from the file, and it cites at least one observation.
- [ ] AC-26 (US-04): Given a run that was cancelled, failed or interrupted, then it creates no findings and earlier findings stay as they were.

## Expected evidence
Tests naming each criterion, and a check that saved finding text holds no column name or file value.

## From planning
- AC-21 is only partly delivered here: possible identifier, significant missing values, single-value column and values that don't fit the type. T09 finishes it.
- The `finding` table has no field for a finding's kind, so this adds one.
- The first slice's AC-33 checks change for the right panel's findings section.
