---
id: CFX-APP-first-slice-T01
title: "Installable runtime serves a page"
feature: CFX-APP-first-slice
feature_revision: 8
stories: [US-01, US-02, US-09]
criteria: [AC-05, AC-07, AC-50]
blocked_by: []
state: done
external_key: null
status: CONFIRMED
---

# Installable runtime serves a page

## Outcome
The `conflux` command installs from locked dependencies, listens only on `127.0.0.1`, and shows a placeholder page from the UI bundle. This is the thin path everything else builds on: one wheel holding the API, the packages and the built UI.

## Acceptance checklist
- [x] AC-05 (US-01): Given `--no-browser`, or a browser that fails to open, when Conflux starts, then it prints the launch URL to the terminal. Given `--port N`, then it listens on that port, and otherwise the OS picks a free port.
- [x] AC-07 (US-02): Given any bind address other than a loopback address, when the runtime tries to start on it, then it refuses.
- [x] AC-50 (US-09): Given a clean checkout, when dependencies are installed from the committed `uv` and `pnpm` lockfiles with frozen settings, then installation succeeds without changing a lockfile.

## Expected evidence
Fresh install from the lockfiles with no lockfile change, and the bundled page loading from the installed wheel. Tests naming each criterion, with output.
