---
id: DRIVN-48
legacy_id: CFX-APP-postgres-T03
title: "Password in the OS credential store"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-01]
criteria: [AC-04, AC-06, AC-30]
blocked_by: [DRIVN-47]
state: blocked
external_key: DRIVN-48
status: CONFIRMED
---

# Password in the OS credential store

Plane: [DRIVN-48](https://app.plane.so/drivnx/browse/DRIVN-48/) · `CFX-APP-postgres-T03`

## Outcome
A saved connection's password goes into Keychain, Windows Credential Manager or Secret Service, so DrivnX reads it after a restart without asking, and asks again only when the server refuses it. No part of the password is anywhere else.

## Acceptance checklist
- [ ] AC-04 (US-01): Given an OS credential store, when a connection is saved, then its password is stored there under an entry naming DrivnX and the source, and DrivnX reads it from there after a restart.
- [ ] AC-06 (US-01): Given a stored password that the server now refuses, when the source is opened, then the user is asked for the password, and saving a new one replaces the stored one.
- [ ] AC-30 (US-01): Given a full journey with a distinctive planted password, then a scan of every file in the workspace, of the logs at their most detailed, of browser storage and of every URL the browser requested finds no part of the password.

## Expected evidence
Tests on each OS CI runs, with the in-memory fallback where no store exists. A scan for a planted password across the workspace, the logs at their most detailed, browser storage and every URL requested.

## Blocked by
DRIVN-47
