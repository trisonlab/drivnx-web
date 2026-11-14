---
id: DRIVN-29
legacy_id: CFX-APP-csv-profile-T09
title: "Possibly sensitive fields"
feature: CFX-APP-csv-profile
feature_revision: 5
stories: [US-04, US-05]
criteria: [AC-21, AC-27, AC-28]
blocked_by: [DRIVN-28]
state: done
external_key: DRIVN-29
status: CONFIRMED
---

# Possibly sensitive fields

Plane: [DRIVN-29](https://app.plane.so/drivnx/browse/DRIVN-29/) · was `CFX-APP-csv-profile-T09`

## Outcome
Columns matching the name or pattern rules get a possibly-sensitive finding, and the screen says that no flag doesn't mean no personal data.

## Acceptance checklist
- [x] AC-21 (US-04): Given a run that meets a finding's condition, then a finding of that kind appears for each column or dataset concerned: possible identifier, significant missing values, single-value column, values that don't fit the type, or possibly sensitive field. A run that doesn't meet a condition creates no finding of that kind.
- [x] AC-27 (US-05): Given synthetic columns matching the name rules or the email, phone or payment card patterns, then each gets a possibly-sensitive finding saying whether a name or a pattern matched. No AI is involved.
- [x] AC-28 (US-05): Given findings for possibly sensitive fields, or none, then the screen says that having no flag doesn't mean a column has no personal data.

## Expected evidence
Synthetic columns for each rule, including ones that shouldn't match.

## From planning
- This finishes AC-21. It's the first thing to cut if the slice grows, by the user's decision. T10's AC-29 depends on it.

## From reviewing T09
- **A name with a number attached matched no rule:** `email2`, `Email1`, `Phone1`, `Address1` and `name2`, while `phone_1` did. A number is now no word in a name, so `email2` reads as `email`. Seen failing first in the name rule test, which also holds `emailed2`, `product_name2` and `mp3` as non-matches.
- **Known limits, accepted:** words alone can over-flag (`cell_type` and `mobile_os` as a phone, `zip_file` and `ip_address` as an address, a bare `Name` as a person's), and space-grouped numbers such as `1 234 567` read as phones. Patterns over 40 characters are saved cut and never matched, and only the most common kept patterns from the first 100,000 rows are checked.
- Three implicit string concatenations in the tests are now parenthesised, for ruff's ISC004.

## Verification
Verified 2026-11-07 on the uncommitted branch `feat/cfx-app-csv-profile-t09-possibly-sensitive-fields`, against spec revision 4.

| Criterion | Evidence | Result |
|---|---|---|
| AC-21 | One possibly-sensitive finding per column concerned, and none where no rule matches: the name, email, phone and card rule tests with their near misses, `test_a_column_matching_by_name_and_pattern_gets_one_finding_saying_both`, the fixed-order test, and `test_columns_matching_a_sensitive_field_rule_are_flagged_saying_what_matched` through a real run, all passing. With T08, every kind is now delivered. | PASS |
| AC-27 | 34 names, 4 email, 6 phone and 7 card patterns, each with cases that shouldn't match, the saved interpretation saying whether the name, the shape or both matched, and the Vitest tests for each one's observed line and evidence link, all passing. The rules are word lists and regular expressions in `packages/profiling/sensitive.py`; no AI. | PASS |
| AC-28 | The Vitest test "the findings say having no flag doesn't mean no personal data" with a sensitive finding, with only other kinds, and with none, passing. | PASS |

Checks run: `uv run --frozen pytest tests/integration tests/golden` (2180 passed, 6 skipped by design), `uv run --frozen pytest tests/e2e` (63 passed), `pnpm --dir apps/web test` (144 passed), `pnpm --dir apps/web build` (type-check and build, exit 0), `uvx ruff check packages apps tests testdata` (all passed), `git diff --check` (clean), the README's secret scan (clean), `ai-skills knowledge discover`. The full suites ran before the ISC004 change, which only parenthesises three expected strings in tests; those two test files were run again after it (102 passed).

Not verified: CI, as there's no remote yet, and a real browser on the installed wheel.
