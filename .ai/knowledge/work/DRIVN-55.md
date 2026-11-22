---
id: DRIVN-55
legacy_id: CFX-APP-postgres-T10
title: "Findings and sensitive fields"
feature: DRIVN-45
feature_revision: 3
stories: [US-05]
criteria: [AC-26, AC-27]
blocked_by: [DRIVN-53, DRIVN-54]
state: done
external_key: DRIVN-55
status: CONFIRMED
---

# Findings and sensitive fields

Plane: [DRIVN-55](https://app.plane.so/drivnx/browse/DRIVN-55/) · `CFX-APP-postgres-T10`

## Outcome
A user sees the CSV profile's findings for each object, with no possible-identifier finding where a key is declared, and the sensitive-field flags with the extra confirmation before their values.

## Acceptance checklist
- [x] AC-26 (US-05): Given a run that meets a finding's condition, then the CSV profile's findings appear (possible identifier, significant missing values, single-value column, possibly sensitive field, and on a full profile duplicate rows) with their evidence links, and a column with a declared primary key or unique constraint gets no possible-identifier finding.
- [x] AC-27 (US-05), all but the confirmation of a database's values, which DRIVN-56 proves (decision 2): Given columns matching the sensitive-field name rules or the email, phone or payment card patterns, then each gets a possibly-sensitive finding, its values need the extra confirmation, and the screen says that no flag doesn't mean a column has no personal data, as AC-27 to AC-29 of the CSV profile.

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

## Verification (2026-11-22)
On a0d4cc6, with the PostgreSQL fixture and `DRIVNX_REQUIRE_POSTGRES=1`: `pnpm --dir apps/web build` passed; Vitest
245 passed; `pytest tests/integration tests/golden` 2576 passed, 8 skipped (Windows-only and by design); `pytest tests/e2e`
83 passed; the ci.yml scans (detect-secrets, pip-audit, pnpm audit) clean; `ai-skills knowledge check` consistent.
Without `field_id not in keyed` the primary-key, unique-constraint and `findings_for` key tests fail.

| Criterion | Evidence | Result |
|---|---|---|
| AC-26 | `test_postgres_findings.py` (each kind, primary key, unique constraint, composite key, duplicate rows full only, stopped object, cancel, order, object and columns), `test_finding_rules.py`, Vitest in `Postgres.test.tsx`, e2e `test_postgres_findings.py` with the accessibility check | PASS |
| AC-27 | `test_each_sensitive_field_rule_finds_a_possibly_sensitive_field`; the no-flag note in Vitest and e2e. The confirmation of a database's values: DRIVN-56 | PASS, confirmation BLOCKED on DRIVN-56 |
| AC-35, its "opening a finding's evidence" step | e2e `test_a_fast_profiles_findings_say_their_object_and_link_to_its_evidence`; the whole journey is DRIVN-58's | PASS for this step |

## Review fixes (2026-11-22)
The independent review found no defect in the product code. Fixed, each test seen failing without what it guards:
- The evidence-link tests cited the first of two objects, so an ID without the object still passed; they cite the second.
- No test covered the run's results failing to load for a database's findings; one does, and fails without
  `detail.isError`.
- `read_findings`' docstring gives a database run's order.
- DRIVN-56 now lists AC-27's confirmation half, which decision 2 gives it.
- The stopped-object test ran at a quarter-second "minute", which `gaps`, read first on a new session, also overran on
  a busy machine (load 9 on 8 cores: 0 passes in 10). It doesn't time the stop, which `test_postgres_stopping.py`
  does, so its minute is 3 s, against `late_gaps`' 30 s wait: 10 passes in 10 at the same load.

Known limits, not fixed (raised by the review, from earlier tickets' design):
- A database run completed before this change has no findings, and says it found nothing. INFERRED: only development
  workspaces hold such runs, as M4 isn't released.
- An object is told apart on screen by `schema.name`, so schema `a.b` with table `c` and schema `a` with table `b.c`
  would share evidence IDs. Only quoted names with a dot can do this; the server keys by dataset ID.
- Masked patterns, and so the pattern rules, cover text types only (DRIVN-45, the Assumptions' metrics): a
  card number stored as `numeric` is flagged only by its name.
