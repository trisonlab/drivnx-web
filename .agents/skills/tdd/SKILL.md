---
name: tdd
description: Builds behaviour test-first in red-green-refactor cycles - write one small failing test for the next behaviour, watch it fail for the right reason, add the minimum code to pass, then clean up with the tests green. Use when the user asks for TDD or test-first development, or when building new logic, calculations, parsers, validators or business rules.
license: MIT
---

# TDD (test-first)

## Purpose
Let tests drive the design: every line of production code exists because a failing test asked for it, and the suite proves each behaviour as it is added.

## When to use
- "Use TDD", "test-first", red-green-refactor; new rules, calculations, parsers, validators, state machines.

## When NOT to use
- Adding tests to existing code, test strategy, coverage, flaky tests: `testing`.
- Browser end-to-end tests: `frontend-testing`. Finding why something fails: `debugging`.

## Procedure
Before step 1, when the change belongs to a ticket, the current branch must be that ticket's own (its name contains the ticket ID). If it is not, stop and ask the user before writing anything, and offer `work-branch`.

1. **Name the seam**: the public interface the tests will drive (function, endpoint, component, CLI). Tests go through it, never into internals. If the right seam is unclear, ask before writing tests.
2. **List the behaviours** to build as short examples (input -> expected result), smallest first. Include edge cases and error cases. Unclear expectations are questions for the user, not guesses.
3. **Red**: write ONE test for the next behaviour, in the project's test framework and style. Before running it, say which production change would make it fail. Run it and confirm it fails, and fails for the expected reason (an assertion, not an import or syntax error). Expected values come from an independent source (a worked example, the spec), never recomputed the way the code does.
4. **Green**: write the minimum production code that makes it pass. Do not add behaviour no test asks for. Run the whole relevant suite: everything passes.
5. **Refactor**: remove duplication and improve names with the tests green; run them after each change.
6. **Repeat** from step 3, one vertical slice per cycle - never all tests first, then all code. Commit at green points if the user wants commits (`work-branch`).
7. **Report** the cycle honestly: which tests were written, that each was seen failing, and the final run result.

Mock only at system boundaries (external APIs, time, randomness, sometimes the database or filesystem), never your own modules. What makes a test worth keeping, mocking rules and the mutation check: [references/good-tests.md](references/good-tests.md). Excuses for skipping a step and the answers to them: [references/rationalizations.md](references/rationalizations.md).

## Output
Tests and code, plus a short log: `test_x - failed (AssertionError) -> passed`, final suite result with the command used.

## Failure modes
- Writing the implementation first and the tests after, then calling it TDD. If code came first, set it aside and drive it again from tests.
- Never running the test in its red state, or a test that fails for the wrong reason, or passes immediately.
- Big steps: many behaviours per test, or speculative code no test needs; writing all tests up front.
- Tests coupled to internals (private methods, call counts, mocks of your own classes) or tautological assertions.
- Skipping refactor, or refactoring while tests are red.
