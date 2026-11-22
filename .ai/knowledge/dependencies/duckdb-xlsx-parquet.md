---
title: "How DuckDB 2.0.0 reads XLSX (excel extension, not bundled) and Parquet (built in)"
status: INFERRED
updated: "2026-11-14"
evidence:
  - path: pyproject.toml
  - path: uv.lock
  - path: hatch_postgres.py
  - path: docs/adr/0006-postgres-extension-in-the-wheel.md
  - path: packages/connectors/csv_files.py
sources:
  - https://duckdb.org/docs/2.0/core_extensions/excel.html (retrieved 2026-11-14)
  - https://duckdb.org/docs/2.0/guides/file_formats/excel_import.html (retrieved 2026-11-14)
  - https://duckdb.org/docs/2.0/data/parquet/overview.html (retrieved 2026-11-14)
  - https://duckdb.org/docs/2.0/data/parquet/metadata.html (retrieved 2026-11-14)
  - https://github.com/duckdb/duckdb-excel/tree/81bb84b115b97cc4edec4827714cac80da0127e1 (commit "Bump submodules for v2.0.0", 2026-10-14)
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
  escaped, as `_literal` already does for CSV.
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
  `normalised_type` in `csv_files.py` maps several of these (TIMESTAMP_NS, UUID, UINTEGER, nested) to `string`.
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
  without `union_by_name`, and a directory is read recursively [docs]. `_literal` and the existing directory check apply.
- **Encryption:** encrypted files need `encryption_config` [docs]; `encryption_algorithm` shows one is encrypted.

## Open

- Every XLSX behaviour above, run against the bundled extension (NOT RUN here).
- XLSX speed and memory on a large workbook, and how big shared strings behave (spike 1 asks for this).
- Whether a Parquet read with `utf8_validation = 'strict'` quotes the bad value in its message.
- Parquet timings on a cold disk (measured here with the file just written).
