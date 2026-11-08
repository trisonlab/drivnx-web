---
id: DRIVN-42
title: "Integration tests pass on Windows"
feature: CFX-APP-first-slice
feature_revision: 9
stories: [US-09]
criteria: [AC-53]
blocked_by: []
state: ready
external_key: DRIVN-42
status: CONFIRMED
---

# Integration tests pass on Windows

Plane: [DRIVN-42](https://app.plane.so/drivnx/browse/DRIVN-42/)

## Outcome
The Windows CI job is green, so the assumption that Windows is a target stops being NOT RUN for the tests that can run there. Each Windows failure is fixed, or the test is skipped on Windows with its reason.

## Acceptance checklist
- [ ] AC-53 (US-09): Given the repo, then CI configuration runs pytest, Vitest, Playwright, secret scanning and dependency vulnerability checks. Each of those also runs locally with a documented command.

## Expected evidence
A green `tests (windows-latest)` job on the ticket's PR. For each failure below, the cause as found, and whether it was the test or the product.

## Found merging PR #4
Until PR #4, `os.geteuid()` stopped pytest while it collected four integration files on Windows, so the job failed before any test ran. Once they loaded, run 42497324612 had 20 failures (2137 passed, 63 skipped). Ubuntu and macOS pass. The causes below are first guesses from the log, INFERRED until checked:

| Tests | Error | Likely cause |
|---|---|---|
| `test_csv_detection.py::test_awkward_directory_names_open_the_file_they_name` (2), `::test_awkward_file_names_open_the_file_they_name` (6), `test_preview_api.py::test_awkward_file_names_preview_the_file_they_name` (1) | `OSError` 22 / WinError 123 | Names with `*`, `?` or `"`, which Windows doesn't allow. Test only. |
| `test_preview_api.py::test_a_first_quote_after_the_sample_is_still_read_as_a_quote[line break]`, `::test_a_line_break_in_quotes_and_an_uneven_row_can_both_be_shown` | `'a\r\nb'` where `'a\nb'` was expected | The test writes its file in text mode, so Windows turns `\n` into `\r\n`. |
| `test_values_on_request.py::test_a_text_columns_values_are_its_range_and_most_frequent_values` | `RowNotReadable` | Probably the same line endings. |
| `test_workspace_api.py::test_no_ai_mode_opens_a_network_connection` (3) | connections to `127.0.0.1` | On Windows, asyncio's event loop opens a socket pair over 127.0.0.1 for itself. |
| `test_ci.py::test_no_test_file_needs_a_posix_module_just_to_load` | `UnicodeDecodeError: 'charmap'` | Reads a test file without `encoding="utf-8"`. |
| `test_lockfiles.py::test_frozen_install_succeeds_and_leaves_the_lockfiles_unchanged` | `FileNotFoundError` (WinError 2) | Probably runs `pnpm` without its `.cmd` wrapper. |
| `test_fast_profile.py::test_a_run_records_what_it_read_and_how` | the run's saved scope differs | Not known. May be the product: check `modified_at` and the encoding saved on Windows. |
| `test_preview_api.py::test_viewing_pages_stores_no_row_values_and_leaves_no_files` (2) | `PermissionError` 13 | Not known. A file still open when it's deleted may be the product's. |

There's no local Windows, so each attempt is a CI run of about 14 minutes.
