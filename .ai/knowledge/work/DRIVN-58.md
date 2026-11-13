---
id: DRIVN-58
legacy_id: CFX-APP-postgres-T13
title: "Full journey and checks"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-03, US-05, US-07]
criteria: [AC-15, AC-29, AC-35, AC-36]
blocked_by: [DRIVN-48, DRIVN-50, DRIVN-52, DRIVN-56, DRIVN-57]
state: blocked
external_key: DRIVN-58
status: CONFIRMED
---

# Full journey and checks

Plane: [DRIVN-58](https://app.plane.so/drivnx/browse/DRIVN-58/) · `CFX-APP-postgres-T13`

## Outcome
The whole PostgreSQL journey passes against the installed wheel and the fixture, the fixture's statement log shows DrivnX never wrote, a scan for planted values proves the R3 rule, the new screens pass the accessibility check, and the threat register and ADRs record what was built.

## Acceptance checklist
- [ ] AC-15 (US-03): Given the fixture's statement log, when the full journey runs, then DrivnX sent no statement that writes or changes the schema, and every statement carries `application_name` `DrivnX`.
- [ ] AC-29 (US-05): Given a full journey with distinctive planted values in the fixture, including the preview and values on request, then a scan of every file in the workspace, during the run and after DrivnX stops, and of its logs at their most detailed finds no value from the database, except masked patterns and the names of schemas, objects, columns and the connection.
- [ ] AC-35 (US-07): Given the installed wheel and the fixture, then the journey of adding a connection, confirming the write warning, choosing objects, running a fast profile, running a full profile and opening a finding's evidence passes in Playwright from one origin, with an automated accessibility check on the new screens in each state.
- [ ] AC-36 (US-07): Given the threat register and the ADRs, then the "Database or API key leaks" row records what's built and tested, and ADRs record the sampling method from spike 2, the bundled extension and the credential store.

## Expected evidence
Playwright on the installed wheel with axe in each state, the statement log, the planted-value scan during the run and after DrivnX stops, and reading the threat register and the ADRs.

## Blocked by
DRIVN-48, DRIVN-50, DRIVN-52, DRIVN-56, DRIVN-57
