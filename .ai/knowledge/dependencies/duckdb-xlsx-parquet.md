---
title: How DuckDB 2.0.0 reads XLSX (excel extension, not bundled) and Parquet (built in)
status: INFERRED
updated: '2026-11-26'
evidence:
- path: pyproject.toml
  sha256: 44e6c51efc357b05bccb13ace5dd1da37ab07d5858303c7cc8492d621368d91b
- path: uv.lock
  sha256: 71f0537a186d2156f44af20e562dbb391f880e540b9621813cbcb571906d693f
- path: hatch_postgres.py
  sha256: 1b0d0fc26a8a76d33f62dda14623794a84da197094428a799ecff8344f1e8784
- path: docs/adr/0006-postgres-extension-in-the-wheel.md
  sha256: 5db83887b3a05068316af944d09589e3ac35f3f902b90cba25554ab9bf908207
- path: packages/connectors/csv_files.py
  sha256: 085f4ed82bf8e147ad4b653ee8de548b3c939e6aa50734827a79ebfe47e30dad
- path: packages/connectors/parquet_files.py
  sha256: 9722dfa438ff41e74b061172926806c585924d71656990a818bf5e1500e5261c
- path: apps/local-api/local_api/sources_api.py
  sha256: aa95ff219b63b7877e83f70a1f30030c2b5d732f0ebf542f07637e43ada263bc
- path: tests/integration/test_parquet_sources.py
  sha256: a751e9ced00e6875738d12ef8a07a04f6f91bd114639e5ec3e0886f96250399a
- path: tests/integration/test_parquet_preview_api.py
  sha256: 18a2af9220fad7044c5f572ab9fea0f193bca6cd228c45b743f085e78f563749
- path: tests/integration/test_parquet_profile.py
  sha256: 2e8d0aeccfb818981eb3dd95315b75ba147cf7844c39405c6c9d90952fba48ba
- path: docs/adr/0010-parquet-sampling.md
  sha256: a2b002e281db76dda85fb215d57870704fcdbb263ede072a5e0ec70ba082f6a5
sources:
- https://duckdb.org/docs/2.0/core_extensions/excel.html (retrieved 2026-11-14)
- https://duckdb.org/docs/2.0/guides/file_formats/excel_import.html (retrieved 2026-11-14)
- https://duckdb.org/docs/2.0/data/parquet/overview.html (retrieved 2026-11-14)
- https://duckdb.org/docs/2.0/data/parquet/metadata.html (retrieved 2026-11-14)
- https://github.com/duckdb/duckdb-excel/tree/81bb84b115b97cc4edec4827714cac80da0127e1 (commit "Bump submodules
  for v2.0.0", 2026-10-14)
---
# How DuckDB 2.0.0 reads XLSX and Parquet

Researched 2026-11-14 for the M2 "other local sources" spec. DrivnX pins `duckdb==2.0.0` (`pyproject.toml`, `uv.lock`),
and loads no extension by name, with autoinstall and autoload off (`packages/connectors/csv_files.py`, `duckdb_connection`).
External documentation and measurements are not project evidence, so this note stays INFERRED.

Labels: **[measured]** run here on DuckDB 2.0.0, linux_amd64; **[docs]** DuckDB 2.0 docs; **[source]** duckdb-excel at
`81bb84b115`, the extension version the v2.0.0 binaries report; **[inferred]** a conclusion from those.

## Which extension is where [measured]

`duckdb_extensions()` in the installed `duckdb` 2.0.0 wheel, with autoinstall and autoload off:

| Extension | State |
|---|---|
| `parquet` | `STATICALLY_LINKED`, loaded. Nothing to bundle. |
| `excel` | `NOT_INSTALLED`. It has to be bundled like `postgres_scanner` (ADR 0006), or XLSX can't be read. |
| `spatial` (GDAL `st_read`, the older route to XLSX) | `NOT_INSTALLED`. |

Sizes from `https://extensions.duckdb.org/v2.0.0/<platform>/<name>.duckdb_extension.gz`, downloaded 2026-11-14 with
`User-Agent: build`:

| Platform | `excel` `.gz` | `excel` unpacked | sha256 of `.gz` |
|---|---|---|---|
| `linux_amd64` | 4,613,020 | 15,631,366 | `40a170558c7110eed04b631551f9ac13f854747a293986612e9922b6044b3821` |
| `osx_arm64` | 3,233,720 | 11,537,214 | `a9e38c4bf55bd482073f2d40c3a0c1334fadfa819ca1b7a0eb599a38936c4fd1` |
| `windows_amd64` | 14,797,292 | 46,694,934 | `e524d6f66e8d7b546c1038bf402241dc18bd556462f3de677c94f05ceb3a61ec` |

All three binaries' footers name extension version `81bb84b115`, DuckDB `v2.0.0` and carry a `duckdb_signature`. For
comparison, `spatial` on `linux_amd64` is 36,283,733 bytes packed and 108,189,118 unpacked. Bundling `excel` the ADR 0006
way adds about 22.6 MB to the wheel and about 74 MB installed, against 50.3 MB and 157.7 MB for `postgres_scanner`.
[inferred] The hashes are public checksums, like those in `hatch_postgres.py`; pin them only after re-downloading in the
build ticket.

**NOT RUN:** loading `excel` into DuckDB here. Running the downloaded binary was refused by this session's permission
guard, so every XLSX behaviour below comes from the docs and the source, not from a run. The ticket that bundles it must
confirm them.

## XLSX through `read_xlsx` [docs, source]

- **Options** (`ReadXLSX::GetFunction`): `header`, `all_varchar`, `ignore_errors`, `range`, `sheet`, `stop_at_empty`,
  `empty_as_varchar`, `normalize_names`. `normalize_names` is in the source but not the docs page.
- **Path is a glob** (`Bind` calls `GlobFiles`) and only the first match is read, so `*`, `?` and `[` in a path must be
  escaped, as `literal_path` already does for CSV.
- **No way to list sheets.** The first sheet in `workbook.xml` is the default. A wrong name fails with
  `Sheet "x" not found ... Did you mean`. Hidden sheets are read like visible ones (the `state` attribute is ignored);
  chart sheets aren't worksheets and aren't offered. [inferred] DrivnX would list sheets itself by reading
  `xl/workbook.xml` and `xl/_rels/workbook.xml.rels` with Python's `zipfile` and `xml.etree`; the venv's expat is 2.9.0,
  which limits entity expansion. No new dependency.
- **Range sniffing without `range`:** the first row with a non-empty cell, from its first non-empty cell to the next empty
  cell in that row (`RangeSniffer`). A title in A1 above a table gives a one-column range; a gap in the header row cuts the
  table off at the gap.
- **`stop_at_empty` defaults to true without `range`:** reading stops silently at the first wholly empty row. With
  `stop_at_empty = false`, empty rows come back as rows of NULLs.
- **Header detection** (`HeaderSniffer`, mode MAYBE): the first row is a header only if every cell in it is a non-empty
  string. Otherwise names are generated from cell references (`A1`, `B1`) or, with an explicit range, column letters.
  Names are trimmed of Unicode spaces and de-duplicated.
- **Types come from the first data row only** (docs, `SniffHeader`): DOUBLE for numbers, VARCHAR for strings and errors,
  BOOLEAN for `b` cells or text `TRUE`/`FALSE`, DATE/TIME/TIMESTAMP from the cell's number format (built-in ids 14–22, or
  a custom format containing `dd`/`yy` and/or `h`). An empty first cell gives DOUBLE (or VARCHAR with `empty_as_varchar`).
  A later cell that doesn't cast fails the read with `read_xlsx: Failed to parse cell 'B7': <cast error>`, and the cast
  error can quote the value; `ignore_errors` makes it NULL.
- **`all_varchar = true` gives raw stored text:** a date comes back as its serial number (`45292`), a boolean as `1`/`0`,
  a number as Excel stored it (`0.30000000000000004`). [inferred] So the CSV way of profiling, all text then `TRY_CAST`,
  would call every date cell "not fitting"; an XLSX profile needs typed reads per column.
- **Formulas:** only the cached `<v>` is read; `<f>` is ignored. A formula never calculated, or saved by a tool that
  writes no cached value, reads as empty. [inferred from the parser's tags]
- **Merged cells:** not handled; the value is in the top-left cell and the others are empty. [inferred: no `mergeCell`
  handling in the parser]
- **1904 date system:** not handled; such workbooks' dates would be 1,462 days early. Serials before 1900-03-01 are off
  by Excel's leap-year bug. [inferred from `ExcelToEpochUS`]
- **Rich text and phonetic runs:** text runs are concatenated; `rPh` is skipped.
- **Limits:** 16,384 columns, 1,048,576 rows written by Excel (the reader allows more), 32,767 characters a cell.
- **Not readable:** `.xls`, and password-protected `.xlsx`, which is an OLE compound file, not a ZIP (miniz then fails
  with "Failed to open zip for reading"). [inferred] DrivnX can name these by the first bytes `D0 CF 11 E0`.
- **One thread, streaming:** an 8 KB buffer over the decompressed sheet, so the first rows are cheap. The whole shared
  strings table is loaded into memory first, and a full count needs the whole sheet parsed. No row count without a scan.
  [inferred] Cancelling works between chunks of 2,048 rows, like other table functions. Performance is NOT RUN.
- **Replacement scan:** `FROM 'x.xlsx'` calls `read_xlsx`; DrivnX should always call it explicitly.

## Parquet through `read_parquet` [measured unless marked]

- **Metadata without scanning:** `parquet_file_metadata` (exact `num_rows`, `num_row_groups`, `created_by`,
  `encryption_algorithm`), `parquet_schema` (physical, converted and logical types, repetition, nesting) and
  `parquet_metadata` (per row group and column: `stats_min`, `stats_max`, `stats_null_count`, `stats_distinct_count`,
  sizes, compression). On a 1.86 GB, 40-million-row, 326-row-group file: metadata 0.005 s, `DESCRIBE` 0.009 s,
  `count(*)` 0.005 s.
- **Statistics hold real values:** `stats_min`/`stats_max` are values from the data (`name0`, `name99999`). Under ADR 0004
  they can't be saved.
- **Native types are kept:** BIGINT, UTINYINT, UBIGINT, DECIMAL(38,2), DATE, TIME, TIME WITH TIME ZONE, TIMESTAMP WITH TIME
  ZONE, TIMESTAMP_NS, INTERVAL, UUID, BLOB, BOOLEAN, FLOAT, lists, nested lists, structs, maps, JSON. An ENUM written by
  DuckDB reads back as VARCHAR, HUGEINT as DOUBLE. Every one casts to VARCHAR (`[[1, 2], [3]]`, `{'a': {'b': [1]}}`).
  Since DRIVN-61, `normalised_type` in `csv_files.py` maps TIMESTAMP_NS to `timestamp`, TIME WITH TIME ZONE to `time`
  and UINTEGER, USMALLINT and UTINYINT to `integer`. Any list (a type containing `[`), struct, map, UUID and BLOB is
  `string`.
- **Reads (warm page cache, same file):** first 100,000 rows into a temp table 0.058 s; a 50-row page at offset 10 million
  0.033 s; `USING SAMPLE reservoir(100000 rows) REPEATABLE (42)` 4.2 s; `0.25% (system, 42)` 3.2 s (90,112 rows);
  `0.25% (bernoulli, 42)` 3.5 s; `count(DISTINCT)` on a 32-character text column 6.5 s. A random sample still reads the
  whole file, as for CSV.
- **Seeded row-group slices are cheap:** filtering `file_row_number` to row ranges in seeded random row groups, using row
  group sizes from `parquet_metadata`, read one 122,880-row group in 0.038 s and eight 12,500-row slices in 0.085 s.
- **Errors:** a truncated file or a non-Parquet file fails with `No magic bytes found at end of file '<path>'`.
  `s3://` and `https://` paths fail because `httpfs` isn't loaded. Invalid UTF-8 fails by default (`utf8_validation =
  'strict'`) [docs].
- **Paths:** globs are expanded (`we[ir]d*.parquet` matched by pattern), several files with different columns fail
  without `union_by_name`, and a directory is read recursively [docs]. `literal_path` and the existing directory check apply.
- **Encryption:** encrypted files need `encryption_config` [docs]; `encryption_algorithm` shows one is encrypted.

## What DrivnX does with this since DRIVN-61 [code and tests]

Connecting a Parquet file (`sources_api.connect_source`) relies on the facts above as follows:

- **Detection is by content, not by suffix.** The file has to start and end with `PAR1`, or with `PARE` (encrypted
  footer), and be at least 12 bytes long (`parquet_files.is_parquet`). Otherwise it's read as a CSV, so a CSV whose
  text happens to start with `PAR1` stays a CSV.
- **What's read:** `parquet_file_metadata` (`num_rows`, `num_row_groups`, `created_by`) and `DESCRIBE read_parquet`
  (`parquet_files.read_metadata`). `parquet_metadata` and its `stats_*` columns are never queried, so no statistic
  reaches a response, a log or `workspace.db`. Tests plant values in the statistics and check that they appear nowhere.
- **Refusals before DuckDB sees the path:**
  - A remote URL (`s3://`, `https://`, `file://`) is refused with "DrivnX reads files on this computer only". `REMOTE`
    needs a scheme of two or more letters, so `C://` stays a path.
  - A folder is refused, so the recursive read of a directory never happens.
  - An encrypted file is refused by its `PARE` magic.
  - A DuckDB error while reading the metadata becomes "This file isn't a Parquet file", without the path in it.
- **Storage:** migration `0010_xlsx_parquet_sources` adds the `parquet` kind and the `row_count`, `row_group_count` and
  `created_by` columns to `source`.
- **Preview (DRIVN-62):** `parquet_files.read_page` reads a page with `LIMIT ? OFFSET ?` over `read_parquet`, columns
  renamed by position so no name from the file enters the SQL, each value cast to text and cut at 1,001 characters in
  DuckDB. A `TIMESTAMP WITH TIME ZONE` is written in UTC (`SET TimeZone = 'UTC'`), not this computer's zone, and a
  `BLOB` as `\x` and lowercase hex, as the database preview writes `bytea`; both decided with the user on
  2026-11-26. A path with `[`, `*` or `?` is read literally (tested beside a file the pattern would match).
- **Profile (DRIVN-63, ADR 0010):** a fast profile reads 8 slices of 12,500 rows, one in each eighth of the file, each
  starting at a row chosen with the run's seed, through a filter on DuckDB's virtual `file_row_number` (no option
  needed in 2.0.0, and with the file's columns renamed it can't be confused with a column of that name). A file of at
  most 100,000 rows is read whole. A full profile reads every row, with masked patterns from the slices. The row count
  is `parquet_file_metadata`'s `num_rows`, saved as `row_count` with the scope `metadata`. On a 1.02 GB, 24.7 M-row
  file through the UI: fast 1.35 s, full 15.7 to 32.2 s, a cancel 3 s in stopped in 1.4 s.
  A run whose file's columns (names, order or DuckDB types) differ from those saved when it was connected ends
  `failed` with "The file's columns no longer match the saved ones." before anything is measured; a file gone or no
  longer Parquet is refused at the start, as a CSV's is (AC-31, confirmed with the user on 2026-11-26).
  A value DuckDB can't read partway (the footer still reads) ends the run with "DrivnX couldn't read this file.".
  Values on request for a Parquet run are refused with "This Parquet file's values can't be worked out yet." until
  DRIVN-64.
- **One file at a time:** every `read_parquet` passes `hive_partitioning = false` (`ONE_FILE`). Otherwise a folder named
  like `id=x` in the path adds a column, or replaces the file's own `id` with `x`, in both the metadata and the preview
  (found in DRIVN-62's review; partitioned datasets are out of scope).
- **A page's errors:** a footer that no longer reads is "isn't a Parquet file"; a value that can't be read on the way to
  the page (for example invalid UTF-8) is the CSV preview's "A row in this file can't be read, so this page can't be
  shown."; running out of memory is "DrivnX ran out of memory showing this page." (wording agreed 2026-11-26).
- **Not verified:**
  - a file written by parquet-cpp-arrow, as pyarrow isn't installed and tests write their files with DuckDB;
  - plaintext-footer encryption, which starts with `PAR1`, so the `PARE` check doesn't catch it;
  - `C://` on Windows.

## Open

- Every XLSX behaviour above, run against the bundled extension (NOT RUN here).
- XLSX speed and memory on a large workbook, and how big shared strings behave (spike 1 asks for this).
- Whether a Parquet read with `utf8_validation = 'strict'` quotes the bad value in its message.
- Parquet timings on a cold disk (measured here with the file just written).
