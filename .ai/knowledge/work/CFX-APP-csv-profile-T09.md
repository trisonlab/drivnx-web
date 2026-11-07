---
id: CFX-APP-csv-profile-T09
title: "Possibly sensitive fields"
feature: CFX-APP-csv-profile
feature_revision: 4
stories: [US-04, US-05]
criteria: [AC-21, AC-27, AC-28]
blocked_by: [CFX-APP-csv-profile-T08]
state: blocked
external_key: null
status: CONFIRMED
---

# Possibly sensitive fields

## Outcome
Columns matching the name or pattern rules get a possibly-sensitive finding, and the screen says that no flag doesn't mean no personal data.

## Acceptance checklist
- [ ] AC-21 (US-04): Given a run that meets a finding's condition, then a finding of that kind appears for each column or dataset concerned: possible identifier, significant missing values, single-value column, values that don't fit the type, or possibly sensitive field. A run that doesn't meet a condition creates no finding of that kind.
- [ ] AC-27 (US-05): Given synthetic columns matching the name rules or the email, phone or payment card patterns, then each gets a possibly-sensitive finding saying whether a name or a pattern matched. No AI is involved.
- [ ] AC-28 (US-05): Given findings for possibly sensitive fields, or none, then the screen says that having no flag doesn't mean a column has no personal data.

## Expected evidence
Synthetic columns for each rule, including ones that shouldn't match.

## From planning
- This finishes AC-21. It's the first thing to cut if the slice grows, by the user's decision. T10's AC-29 depends on it.
