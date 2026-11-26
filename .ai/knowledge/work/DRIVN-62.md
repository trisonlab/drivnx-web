---
id: DRIVN-62
title: "Parquet: preview"
feature: DRIVN-60
feature_revision: 1
stories: [US-03]
criteria: [AC-10]
blocked_by: [DRIVN-61]
state: done
external_key: DRIVN-62
status: CONFIRMED
---

# Parquet: preview

Plane: [DRIVN-62](https://app.plane.so/drivnx/browse/DRIVN-62/)

## Outcome
A user opens a Parquet file's preview and pages through its rows, 50 a page or up to 200 when asked, with each cell cut at 1,000 characters. Nothing from the preview is saved, and no response is cached.

## Acceptance checklist
- [x] AC-10 (US-03, this ticket's part: a Parquet file; a sheet's is DRIVN-67's): Given a chosen sheet or a Parquet file, when the user opens its preview, then 50 rows a page are shown (up to 200 when asked), each cell cut at 1,000 characters, every response sent with `Cache-Control: no-store`, and no row of `workspace.db` holds a value from the preview.

## Expected evidence
API tests: page sizes, the 1,000-character cut, `Cache-Control: no-store`, and a raw scan of `workspace.db` and its WAL file finding no previewed value. Vitest for the preview of a Parquet source. e2e opening it.

## Notes
The CSV preview's paging and limits are reused as they are.

## Blocked by
DRIVN-61.

## Verification (2026-11-26, on `feat/drivn-62-parquet-preview` from `8f348cf`, uncommitted)
| Criterion | Evidence | Result |
|---|---|---|
| AC-10: 50 rows a page | `test_parquet_preview_api.py::test_the_preview_opens_on_the_first_50_rows`: 120 rows, columns `id` and `name`, rows 0 to 49, a next page; failed first with the interim 409. e2e `test_connect_parquet.py::test_paging_through_a_parquet_file` on the installed wheel: "Rows 1 to 50", 51 rows with the header, Next to "Rows 51 to 100", axe clean; failed with `Shell.tsx` reverted | PASS |
| AC-10: up to 200 when asked | `test_up_to_200_rows_a_page_when_asked`: 200 rows with a next page, 201 refused with 422, the last page and past it. Passed at once (the CSV route's limits); failed when the reader was made to fetch `limit` rows, not `limit + 1` | PASS |
| AC-10: each cell cut at 1,000 characters | `test_a_cell_over_1000_characters_is_cut_and_marked`: 1,000 kept whole, 1,001 and 50,000 cut to 1,000 and marked. Passed at once: `shown_cell` is the CSV preview's | PASS |
| AC-10: every response `Cache-Control: no-store`; no row of `workspace.db` holds a previewed value | `test_a_preview_is_never_stored_by_drivnx_or_the_browser`: `no-store`; two planted values in the response and absent from the raw bytes of every workspace file after a WAL checkpoint, nothing in `duckdb-tmp` or the working folder. Passed at once, as nothing on this path writes | PASS |
| Expected evidence: Vitest for a Parquet source's preview | `ParquetDetails.test.tsx` "a Parquet file's preview opens on its first page"; failed with no Preview region | PASS |

Also built, by the user's decisions in chat on 2026-11-26: a `TIMESTAMP WITH TIME ZONE` shows in UTC, not this
computer's zone (`test_a_moment_shows_in_utc_as_the_file_stores_it`, failed with New York's time), and a `BLOB` as
`\x` and lowercase hex, as the database preview shows `bytea` (`test_binary_shows_every_byte_in_hex_as_a_database_preview_does`,
failed with DuckDB's `\xDE\xADA`). Errors are the CSV preview's, without the path: a file gone, a file DrivnX may not
read, and a file replaced by text or cut short (each failed first with DuckDB's message naming the path). Column names
are never written into the SQL, an empty file shows its columns, and a path with `[`, `*` or `?` previews only itself
(failed with `literal_path` removed). The interim "This Parquet file can't be previewed yet." is gone.

After the review (below): a folder named like `id=x` adds no column to the preview or the metadata
(`test_a_folder_named_like_a_partition_adds_nothing_to_the_file`, `test_a_folder_named_like_a_partition_adds_no_column`,
both failed with DuckDB's Hive partitioning on); a damaged value says "A row in this file can't be read, so this page
can't be shown." (`test_a_damaged_value_says_a_row_cant_be_read_without_the_value_or_path`, failed with "isn't a
Parquet file"); running out of memory says "DrivnX ran out of memory showing this page."
(`test_running_out_of_memory_says_so`, DuckDB given 8 MB, failed with the row message).

Checks run, on the tree above, after the review's fixes (run started 02:44:34; no file changed since):
- `uv run --frozen pytest tests/integration tests/golden`: 2671 passed, 8 skipped (the same platform and by-design
  skips as DRIVN-61; the PostgreSQL fixture was up), exit 0, in 8 min 43 s.
- `uv run --frozen pytest tests/e2e`: 99 passed, exit 0, in 3 min 48 s.
- `pnpm --dir apps/web exec tsc --noEmit`: exit 0. `pnpm --dir apps/web test`: 266 passed, 4 runs of 4 (`apps/web` unchanged by the review's fixes).
- `ai-skills knowledge check --stamp`: the DuckDB note re-checked against the changed code and stamped; only
  `index/graph.yaml` is out of date.

Not verified:
- A file written by another writer than DuckDB (no pyarrow here), as for DRIVN-61.
- An encrypted file swapped in after connecting says "isn't a Parquet file", not that it's encrypted.
- A `BLOB` inside a list or struct is written as DuckDB writes it, with doubled backslashes and printable bytes as
  letters (`[from_hex('DEAD41')]` shows `['\\xDE\\xADA']`), not in hex: only top-level `BLOB` columns are converted.
- CI on GitHub, the e2e on macOS and Windows, and the UTC test on Windows (it changes the process's zone, POSIX only).
- A file with no columns or with duplicate column names: DuckDB can't write either, and there's no other writer here.

## Review (2026-11-26, the reviewer agent, on the uncommitted diff)
Verdict "ready after fixes"; AC-10's parts all met. Each finding was reproduced here before it was fixed test-first:
1. Risk: `read_parquet` read a folder named like `id=x` as a column, so `…/id=x/f.parquet` holding `id = 1` previewed
   as `x`, and its metadata listed `id` as `VARCHAR`. Fixed with `hive_partitioning = false` in every read
   (`ONE_FILE`), in the preview and, by the user's choice, in DRIVN-61's metadata read too.
2. Risk: every DuckDB error became "isn't a Parquet file", including a damaged value in a file whose metadata reads,
   and running out of memory. Now a failing `DESCRIBE` keeps that message; a failing page read says the CSV preview's
   "A row in this file can't be read, so this page can't be shown.", and running out of memory "DrivnX ran out of
   memory showing this page." (wording chosen by the user).
3. Risk: no tests for 1 and 2; the four tests above.
4. Nit: the BLOB example under "Not verified" was wrong; corrected.

Left as they were (outside this ticket): the CSV preview also turns running out of memory into its row message; a
file swapped for a FIFO between the availability check and the read would block a worker, as DuckDB's own open would.

