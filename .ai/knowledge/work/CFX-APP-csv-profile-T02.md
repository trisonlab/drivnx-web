---
id: CFX-APP-csv-profile-T02
title: "Correct how a file is read"
feature: CFX-APP-csv-profile
feature_revision: 2
stories: [US-01]
criteria: [AC-01, AC-02, AC-03, AC-05]
blocked_by: []
state: done
external_key: null
status: CONFIRMED
---

# Correct how a file is read

## Outcome
A user can change a CSV's header setting, delimiter, encoding and lines to skip, and sees the columns and preview follow. The corrected settings survive a restart and reconnecting the same path.

## Acceptance checklist
- [x] AC-01 (US-01): Given a connected CSV, when the user opens its settings, then the current header setting, delimiter, encoding and lines to skip are shown and can be changed.
- [x] AC-02 (US-01): Given a file read with the wrong delimiter, header setting, encoding or skipped lines, when the user saves the right value, then the columns and the preview show the file read that way, and restarting keeps it.
- [x] AC-03 (US-01): Given settings that can't read the file, or an encoding over the 100 MB limit, when the user saves, then they get a clear message and the previous settings stay in use.
- [x] AC-05 (US-01): Given a path already connected with corrected settings, when it is connected again, then the existing source opens with the corrected settings.

## Expected evidence
Tests naming each criterion, across the API and the settings form, using the first slice's files that detection gets wrong.

## From planning
- `read_dataset` expects exactly one dataset per source, and existing tests rely on it. A correction adds a dataset, so a source needs a current one first.
- Skipped lines are worked out on every read and aren't saved. A correction needs a saved setting that overrides this.
- `_current_encoding` detects the encoding again when a file changes, which would overwrite a user's choice. A corrected encoding must stay as set.

## Decisions in building this
- **A source's current dataset is the one with no `replaced_at`,** and a unique index means there can only be one. New settings or new columns add a dataset, and saving the same settings on the same columns doesn't.
- **Lines to skip stay automatic unless the user changes them.** The form sends `null` when that field wasn't touched, because the number can change once the delimiter is right.
- **A skip that leaves uneven lines above the table needs its own read.** DuckDB's sniffer can't read these even with padding, so `_read_uneven_page` turns it off and gives the columns. The width comes from a strict sniff, then a lenient one, because a lenient sniff can settle on a title line's width. With a header, the names come from that first row, with blanks generated and repeats numbered. A row wider than the table is an error on the page that reaches it, and is never cut. Columns read this way are all `VARCHAR`.
- **Mixed line endings with a chosen number of lines are refused.** Only a lax read manages those files, and when told how many lines to skip it can answer with no rows and the wrong columns. These files can still have their delimiter, header and encoding corrected while skipping stays automatic.
- **The whole file is checked when UTF-8 is chosen,** so a page far into the file can't fail after the save worked. A NUL byte is refused for every encoding except UTF-16. A wrong choice that still decodes, such as Latin-1 for a UTF-8 file, is accepted and shows garbled text, as nothing can tell it's wrong.
- **The form only appears when opened,** so the option names don't repeat what the page already shows, and no rows are read for it until then.

## For later tickets
- T05: profiling must read files the way the preview does, including through `_read_uneven_page` when a chosen skip needs it.
