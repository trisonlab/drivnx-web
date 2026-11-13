---
id: DRIVN-55
legacy_id: CFX-APP-postgres-T10
title: "Findings and sensitive fields"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-05]
criteria: [AC-26, AC-27]
blocked_by: [DRIVN-53, DRIVN-54]
state: blocked
external_key: DRIVN-55
status: CONFIRMED
---

# Findings and sensitive fields

Plane: [DRIVN-55](https://app.plane.so/drivnx/browse/DRIVN-55/) · `CFX-APP-postgres-T10`

## Outcome
A user sees the CSV profile's findings for each object, with no possible-identifier finding where a key is declared, and the sensitive-field flags with the extra confirmation before their values.

## Acceptance checklist
- [ ] AC-26 (US-05): Given a run that meets a finding's condition, then the CSV profile's findings appear (possible identifier, significant missing values, single-value column, possibly sensitive field, and on a full profile duplicate rows) with their evidence links, and a column with a declared primary key or unique constraint gets no possible-identifier finding.
- [ ] AC-27 (US-05): Given columns matching the sensitive-field name rules or the email, phone or payment card patterns, then each gets a possibly-sensitive finding, its values need the extra confirmation, and the screen says that no flag doesn't mean a column has no personal data, as AC-27 to AC-29 of the CSV profile.

## Expected evidence
Integration tests for each finding kind on the fixture, a declared key, and each sensitive-field rule.

## Blocked by
DRIVN-53, DRIVN-54
