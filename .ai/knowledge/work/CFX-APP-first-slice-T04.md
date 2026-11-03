---
id: CFX-APP-first-slice-T04
title: "Set up a workspace in the app shell"
feature: CFX-APP-first-slice
feature_revision: 4
stories: [US-01, US-03, US-07]
criteria: [AC-01, AC-14, AC-15, AC-16, AC-32, AC-33, AC-34]
blocked_by: [CFX-APP-first-slice-T02, CFX-APP-first-slice-T03]
state: done
external_key: null
status: CONFIRMED
---

# Set up a workspace in the app shell

## Outcome
On first launch the user sees where the workspace is stored, names it and picks an AI mode, then lands in the three-panel shell with the privacy indicator. Anything not built is visibly marked. Reopening skips setup.

## Acceptance checklist
- [x] AC-01 (US-01): Given Conflux is installed and `~/Conflux/default` doesn't exist, when the user runs `conflux`, then that workspace is created, a browser opens on `http://127.0.0.1:<port>/`, the app loads signed in, and the launch code is gone from the address bar.
- [x] AC-14 (US-03): Given a newly created workspace, when the app first loads, then it shows the workspace's location and states that no credentials are stored in this slice, and asks for a name and an AI mode (off, external or local), with off selected.
- [x] AC-15 (US-03): Given setup is complete, when the runtime is restarted on the same directory, then the workspace's name, AI mode and sources are as they were, and setup isn't shown again.
- [x] AC-16 (US-03): Given any AI mode is selected, then no AI request is made, because AI arrives in M8.
- [x] AC-32 (US-07): Given the app is open, then it shows a header with workspace name, profile status, AI mode, privacy indicator and export, a left Data Explorer, centre tabs (Overview, Profile, Dictionary, Contract) and a right Intelligence & Review panel.
- [x] AC-33 (US-07): Given anything not yet built (profile status, export, the tabs and the right panel), then it is visibly marked as not available yet and has no working action.
- [x] AC-34 (US-07): Given AI mode is off, then the privacy indicator reads "Processing: Local · External AI: Off". Given another mode is selected, then the indicator shows that mode.

## Expected evidence
Tests naming each criterion, and a Playwright run of the first launch and a restart. AC-15 is rechecked for sources once T05 exists.

## From T02
- AC-13 is only proven for the launch code so far, because no workspace data exists yet. Extend `test_get_and_head_routes_change_nothing` with a snapshot of workspace data here and in T05.
