---
id: DRIVN-55
legacy_id: CFX-APP-postgres-T10
title: "Findings and sensitive fields"
feature: CFX-APP-postgres
feature_revision: 3
stories: [US-05]
criteria: [AC-26, AC-27]
blocked_by: [DRIVN-53, DRIVN-54]
state: ready
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

## Plan agreed with the user
Approved in chat, 2026-11-21, with four decisions:
1. **Keys:** a column gets no possible-identifier finding when it is, alone, the primary key or a unique constraint
   (the spec's "a declared primary key or unique constraint ... on its column"). A column of a composite key can.
2. **AC-27's extra confirmation:** this ticket saves the possibly-sensitive flag, which the values route already checks
   before answering. A database's values on request come with DRIVN-56, which proves the confirmation.
3. **Wording:** the duplicate-rows and single-value texts say nothing of a file, for a CSV too. Saved findings keep theirs.
4. **Order:** a database run's findings by object in the run's order, each object's in the fixed order by kind.

Approach: the CSV profile's rules (`profiling.findings.findings_for`) applied to each object of a database run, from
its saved observations, as a CSV's are. No migration: the kinds are in `0006_finding_kind.py`'s list.

Steps:
1. `findings_for(observations, names, *, keyed=frozenset())`: no possible identifier for a field in `keyed`; values that
   don't fit only where that metric is saved; nothing for an object with no `rows_read` (stopped at its time limit);
   the two texts neutral. Tests in `test_finding_rules.py`.
2. `ChosenObject.keys`, read by `read_chosen_objects`; the database job saves each object's findings with a completed
   run, none with a cancelled or failed one. Tests in `test_postgres_findings.py` on the fixture: each kind, a primary
   key, a unique constraint, each sensitive-field rule, duplicate rows on a full profile only, a stopped object.
3. `/api/findings` for a database run: each cited observation named by its own object's columns, with `object`.
4. The findings panel for a database: each finding's object, "observed in" by that object's reading, evidence links to
   that object's cells, the database's empty text, the personal-data note. Vitest.
5. e2e: a finding on a fast profile, its evidence link, the accessibility check.
6. The threat register where it covers the sensitive-field rules, the ticket and the knowledge index.

Step 6, the threat register: no row covers the sensitive-field rules, and DRIVN-29 added none for a CSV. The nearest,
R3 (values not saved), is proved for PostgreSQL by DRIVN-58's planted-value scan (AC-29); a database run's saved
findings hold no name or value, as a CSV's don't. So the register is unchanged.

Not in this ticket: values on request and their confirmation for a database (DRIVN-56), earlier runs (DRIVN-57),
new kinds of finding or sensitive-field rules, ranking findings across objects (M5).
