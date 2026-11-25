---
id: DRIVN-70
title: "File browser"
feature: DRIVN-60
feature_revision: 1
stories: [US-05]
criteria: [AC-18, AC-19, AC-20, AC-32]
blocked_by: []
state: ready
external_key: DRIVN-70
status: CONFIRMED
---

# File browser

Plane: [DRIVN-70](https://app.plane.so/drivnx/browse/DRIVN-70/)

## Outcome
A user picks a file by browsing instead of typing its path: the browser starts in the home folder, shows folders and CSV, XLSX and Parquet files with their size and modified time, leaves hidden ones out, lists drives on Windows, goes up to the top of the drive, follows a link to a folder, and shows at most 1,000 entries. Choosing a file connects it as typing its path does.

## Acceptance checklist
- [ ] AC-18 (US-05): Given a home folder holding `Documents/`, `.ssh/`, `events.parquet`, `notes.txt` and `sales.xlsx`, when the user opens the file browser, then it shows `Documents/`, then `events.parquet` and `sales.xlsx` with their size and modified time; `.ssh/` and `notes.txt` aren't shown. A folder or file the system marks hidden isn't shown either. On Windows, the browser also lists the drives, such as `C:\` and `D:\`. Choosing `sales.xlsx` connects it, as typing its path does.
- [ ] AC-19 (US-05): Given a folder of 1,500 entries it would show, then the first 1,000 are shown, folders first and then by name, and "500 more aren't shown. Type the path to open one of them." Given a folder DrivnX can't read, when the user opens it, then "DrivnX can't open this folder." is shown and the browser stays on the folder it was in.
- [ ] AC-20 (US-05): Given the folder listing route, then a request without the session, with another Host or with another Origin is refused as every other route's is; a response holds names, kinds, sizes and modified times only, never a byte of a file; and listing folders writes nothing to `workspace.db`.
- [ ] AC-32 (US-05): Given the file browser, when the user goes up from the home folder, then it shows the parent folder, up to the top of the drive or `/`. Given a link to a folder, then it opens the folder the link points to.

## Expected evidence
API tests: what's listed and left out, order and the 1,000-entry limit with its message, an unreadable folder, going up, a link, and the session, Host and Origin checks; nothing written to `workspace.db`, and no file's bytes in a response. Windows' drives on Windows CI. Vitest for the browser. e2e choosing a file. A 1,000-entry folder listed within 1 s.

## Notes
Not blocked by the XLSX tickets: choosing a file uses the connect route, so a workbook connects once DRIVN-66 is merged, and DRIVN-72's journey chooses `sales.xlsx`.

## Blocked by
None, it can start now.
