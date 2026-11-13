---
id: DRIVN-46
legacy_id: CFX-APP-postgres-T01
title: "Fixture and bundled extension"
feature: CFX-APP-postgres
feature_revision: 2
stories: [US-01, US-07]
criteria: [AC-07, AC-34]
blocked_by: []
state: ready
external_key: DRIVN-46
status: CONFIRMED
---

# Fixture and bundled extension

Plane: [DRIVN-46](https://app.plane.so/drivnx/browse/DRIVN-46/) · `CFX-APP-postgres-T01`

## Outcome
The installed wheel attaches a PostgreSQL database read-only, loading DuckDB's PostgreSQL extension from inside the package with its signature checked and no download, against a Docker Compose fixture built from synthetic data. The spike's measurement runs against the fixture. This settles how CI runs PostgreSQL on macOS and Windows, where GitHub's runners can't run the Linux image.

## Acceptance checklist
- [ ] AC-07 (US-01): Given the installed wheel on each tested platform, when DrivnX connects, then it loads the PostgreSQL extension from inside the package with DuckDB's signature check on, and DuckDB makes no request to any host other than the database server. Given another platform, then the screen says PostgreSQL isn't available on it.
- [ ] AC-34 (US-07): Given `testdata/` and the fixture, then the fixture is a Docker Compose service with a pinned image, its database is built by a committed script from synthetic data only, and tests that need it say so when it isn't running.

## Expected evidence
The installed wheel attaching the fixture with downloads off, on each tested platform, and the platform message elsewhere. The fixture's script and pinned image. `tests/measure_postgres_sample.py` run against the fixture. CI on Ubuntu, macOS and Windows, or NOT RUN with the reason where PostgreSQL can't run.

## Notes
AC-07 is shared with DRIVN-47: this ticket proves the extension loads from the package; DRIVN-47 proves it when a user connects.

## Blocked by
None - can start now.
