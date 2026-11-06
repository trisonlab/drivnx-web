# 0004 — What a profile saves: aggregates only, with values computed on request and no cache

- **Status:** Accepted 2026-11-05
- **Date:** 2026-11-05
- **Source:** The user's decisions in chat on 2026-11-05, recorded in spec `CFX-APP-csv-profile`; direction doc Sections 5,
  7 and 11; ADR 0002 (the finding on values from source data, and "observations are always stored"); ADR 0003 (accepted
  values); R3 in the threat register

## Context

The threat register's R3 and ADR 0002 both ask for a rule before M3. Min and max, frequent values, pattern examples and
histograms are all copied from source data, and they conflict with "raw data not copied by default" in Section 7. Those
values often include personal data, and a workspace can be copied or stolen.

Section 11 also leaves profile caching open: recompute, or keep results. ADR 0002 has already settled part of it.
Observations are evidence, so they're always stored and can't become recompute-only.

## Decision

**1. A profile saves only aggregates.** An observation can hold a count, a percentage, a text length or a masked pattern.
In a masked pattern, letters in any script become `A` and digits in any script become `9`, and other characters stay.
A combining mark after a letter counts as part of it, so `é` gives one `A` whether it's written as one character or two. A
mark with no letter before it stays, as other characters do, and text written differently can still give different patterns:
Korean `민수` gives `AA` composed and `AAAAA` decomposed. Numbers that aren't decimal digits, like `½` and `Ⅷ`, become `9`
too, as the pattern would otherwise be the value itself. Characters too new for DuckDB's Unicode tables become `A`, digits
included, for the same reason (decided building T05 of the spec, 2026-11-05). A column keeps its 10 most common patterns, and
a pattern is cut at 40 characters and marked as cut.

**2. Values from the file are computed on request and never saved.** This covers min, max, frequent values, histograms,
mean, median and standard deviation. Histograms are included because their bucket edges reveal real values. Mean and
median are included because they can be, or can reveal, a real value.

**3. Values that aren't saved aren't observations, so findings can't cite them.** A finding can still use a count, such
as how many values don't fit the column's type. This keeps ADR 0002's rule that observations are always stored.

**4. A saved finding holds no column name and no value from the file.** The column comes from the observation it cites,
so a name that's replaced later can't survive in a finding.

**5. A correction that changes column names replaces the earlier names with generated ones.** A row wrongly used as
column names may hold personal data. The replacement uses `PRAGMA secure_delete` and a WAL checkpoint, so a raw scan of
the database and its WAL file no longer finds the old names.

**6. There's no profile cache.** Every run computes its results afresh and saves its observations, and results aren't
reused between runs. On-request values are computed each time. Nothing is kept beside the database.

## Alternatives considered

| Option | Why not |
|---|---|
| Save values, except for fields flagged as sensitive (suggested in ADR 0002) | Detection can't prove a field is safe, so a missed field's values would be saved. |
| Save masked or limited values (suggested in ADR 0002) | Masking a min or a frequent value leaves little use in it, and a limit still copies some data. |
| Compute on request, and cache the result for a time | It's still a copy of source data on disk, and the queries measured are quick enough without one. See "Measured". |
| Reuse results between runs when a file looks unchanged | Size and modified time can't prove a file is unchanged, and a reused result would no longer be evidence from that run. |

## Measured

On 2026-11-05, on this machine's NVMe disk, with the page cache dropped first and DuckDB limited to 4 GB. The figures are
for the queries alone, with nothing saved and no UI, on 1 GB, 5-column files. There were two runs each of the typical
files and one of the all-unique file. T12 of the spec measures the real product.

| File | Fast profile | Full profile, duplicates and patterns | Peak memory |
|---|---|---|---|
| Typical, UTF-8 | 1.00, 0.74 s | 16.70, 17.13 s | 3.06 GB |
| Typical, Latin-1 | 1.19, 1.21 s | 20.47, 19.29 s | 3.07 GB |
| All unique text, UTF-8 | 2.53 s | Not run together: full 6.64 s, duplicates 1.83 s | 2.28 GB |

An interrupt stopped a whole-file query within 0.04 s in all five tries, each 1.5 s into reading the file. All of this is
within the spec's targets of 5 s for a fast profile, 60 s for a full profile, and 5 s to cancel.

## Consequences

- **What this costs users:** seeing a column's range or frequent values needs the file to still be readable, re-reads
  it, and can give a different answer from the run if the file has changed.
- **What this costs findings:** a finding can't say what a value is. "1,200 dates are in the future" works, and "the
  latest date is 2031-01-01" doesn't.
- **Masked patterns still say something about the data,** such as a value's length and its punctuation. A column whose
  values are all different gives only their shapes, and this is accepted.
- **What stays open:**
  - A row used as column names that the user never corrects stays saved, and the screen says a row from the file was
    used. This is the one known exception.
  - Pre-migration backups can keep names replaced after they were taken.
  - `DictionaryEntry.examples` (ADR 0002) and contract accepted values (ADR 0003) aren't covered. Both are chosen or
    typed by a user, and their rule is due with the dictionary and contract slices, not before M3 as ADRs 0002 and 0003
    first said.
- **Spill files are unchanged.** DuckDB's spill files can hold source data for the length of a job (R4, accepted).
- **Tests that ship with the spec:** a full journey with distinctive planted values, including on-request values, after
  which a raw scan of `workspace.db` and its WAL file finds no value from the file, except masked patterns and column
  names (AC-33). A correction leaves no earlier name in either file (AC-04), and a saved finding holds no column name or
  file value (AC-25).
- **Revisit when:** users need a saved range or frequent values often enough to accept the risk, for example as an
  opt-in per field, or bulky results turn out to be too slow to compute on request.
