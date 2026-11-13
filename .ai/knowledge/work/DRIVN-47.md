---
id: DRIVN-47
legacy_id: CFX-APP-postgres-T02
title: "Add a connection"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-01, US-03]
criteria: [AC-01, AC-02, AC-03, AC-05, AC-07, AC-14]
blocked_by: [DRIVN-46]
state: blocked
external_key: DRIVN-47
status: CONFIRMED
---

# Add a connection

Plane: [DRIVN-47](https://app.plane.so/drivnx/browse/DRIVN-47/) · `CFX-APP-postgres-T02`

## Outcome
A user adds a PostgreSQL connection in the Data Explorer, tests it, sees the server's version and whether it's encrypted, and finds it again after a restart, asked for the password then. Every session DrivnX opens is read-only.

## Acceptance checklist
- [ ] AC-01 (US-01): Given the Data Explorer, when the user adds a PostgreSQL connection, then they enter host, port (5432 by default), database, user, password and TLS mode (`prefer` by default, `require`, or `verify-full` with a CA certificate file), and there's no field for a connection string or URL.
- [ ] AC-02 (US-01): Given details that work, when the user tests or saves the connection, then DrivnX connects within the connect timeout and shows the server's version and whether the connection is encrypted, and a saved source appears in the Data Explorer under its database name and stays after a restart.
- [ ] AC-03 (US-01): Given a host that can't be reached, refused credentials, a database that doesn't exist, or TLS that is required and fails, when the user tests or saves, then a specific message says which, within the connect timeout, and no source is saved.
- [ ] AC-05 (US-01): Given no OS credential store, when a connection is saved, then the screen says the password is kept only until DrivnX stops, and after a restart opening the source asks for it before anything is read.
- [ ] AC-07 (US-01): Given the installed wheel on each tested platform, when DrivnX connects, then it loads the PostgreSQL extension from inside the package with DuckDB's signature check on, and DuckDB makes no request to any host other than the database server. Given another platform, then the screen says PostgreSQL isn't available on it.
- [ ] AC-14 (US-03): Given any connection DrivnX opens, then the session is read-only, and an attempt to write through it is refused by the server.

## Expected evidence
API and Playwright tests against the fixture for each field, each failure message within the connect timeout, and a restart. A write through DrivnX's connection refused by the server.

## Blocked by
DRIVN-46
