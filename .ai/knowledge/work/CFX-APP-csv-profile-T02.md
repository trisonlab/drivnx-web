---
id: CFX-APP-csv-profile-T02
title: "Correct how a file is read"
feature: CFX-APP-csv-profile
feature_revision: 1
stories: [US-01]
criteria: [AC-01, AC-02, AC-03, AC-05]
blocked_by: []
state: ready
external_key: null
status: CONFIRMED
---

# Correct how a file is read

## Outcome
A user can change a CSV's header setting, delimiter, encoding and lines to skip, and sees the columns and preview follow. The corrected settings survive a restart and reconnecting the same path.

## Acceptance checklist
- [ ] AC-01 (US-01): Given a connected CSV, when the user opens its settings, then the current header setting, delimiter, encoding and lines to skip are shown and can be changed.
- [ ] AC-02 (US-01): Given a file read with the wrong delimiter, header setting, encoding or skipped lines, when the user saves the right value, then the columns and the preview show the file read that way, and restarting keeps it.
- [ ] AC-03 (US-01): Given settings that can't read the file, or an encoding over the 100 MB limit, when the user saves, then they get a clear message and the previous settings stay in use.
- [ ] AC-05 (US-01): Given a path already connected with corrected settings, when it is connected again, then the existing source opens with the corrected settings.

## Expected evidence
Tests naming each criterion, across the API and the settings form, using the first slice's files that detection gets wrong.

## From planning
- `read_dataset` expects exactly one dataset per source, and existing tests rely on it. A correction adds a dataset, so a source needs a current one first.
- Skipped lines are worked out on every read and aren't saved. A correction needs a saved setting that overrides this.
- `_current_encoding` detects the encoding again when a file changes, which would overwrite a user's choice. A corrected encoding must stay as set.
