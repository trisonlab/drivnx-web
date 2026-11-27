---
title: Architecture decision index
status: CONFIRMED
generated_by: ai-skills knowledge discover
evidence:
- path: docs/adr/0001-workspace-packaging-and-session-token.md
  sha256: c7aefa50fb13bd077a656403b33297704d19b0f350ccdecf82f1c7b4156289a6
- path: docs/adr/0002-workspace-storage-sqlite-only.md
  sha256: 8dc9aa9e1577816cf89d28430561abdd76a600a310e19d4d0f72648a8f6184be
- path: docs/adr/0003-contract-schema-native-with-odcs-export.md
  sha256: 7e66661935fb8164735a40415271532c2b582a649019ce6877815c47bca0954e
- path: docs/adr/0004-profile-values-and-caching.md
  sha256: 4446f4336c249b89cf73f51bd2cbbd788dcba6319062e37483bf69e396c5014a
- path: docs/adr/0005-postgres-sampling.md
  sha256: 1d789e593fde8aa7b2400ca4f1ac106a15f62968f7e3d0e8827ea9fbb7461e16
- path: docs/adr/0006-postgres-extension-in-the-wheel.md
  sha256: 5db83887b3a05068316af944d09589e3ac35f3f902b90cba25554ab9bf908207
- path: docs/adr/0007-postgres-password-and-connection.md
  sha256: 9c7fd883f179d3c3cad967701e51e0eeeca54e2004058300f734653c144883df
- path: docs/adr/0008-postgres-password-in-the-os-credential-store.md
  sha256: c0e5766fe4c216f6bd01533a6945277047ed7db71f6681d84f1a06635a73ddff
- path: docs/adr/0009-stopping-a-postgres-read.md
  sha256: 66f8d976394f3136b23e73319e1efefcddabac3fd53e0814a364e39efee6ebd7
- path: docs/adr/0010-parquet-sampling.md
  sha256: a2b002e281db76dda85fb215d57870704fcdbb263ede072a5e0ec70ba082f6a5
---
# Architecture decisions

Index of the project's ADRs. Status comes from each file; the ADR text is the authority.

| Decision | Status | File |
|---|---|---|
| 0001 — Workspace packaging: CLI launcher, and how the browser gets the session token | unknown | `docs/adr/0001-workspace-packaging-and-session-token.md` |
| 0002 — Workspace storage: SQLite only; files exist only as explicit exports | unknown | `docs/adr/0002-workspace-storage-sqlite-only.md` |
| 0003 — Contract internal schema: DrivnX-native rules, ODCS 3.2.0 as an export format | unknown | `docs/adr/0003-contract-schema-native-with-odcs-export.md` |
| 0004 — What a profile saves: aggregates only, with values computed on request and no cache | unknown | `docs/adr/0004-profile-values-and-caching.md` |
| 0005 — How a profile reads a PostgreSQL table: page samples with a seed, one connection, DrivnX's own time limit | unknown | `docs/adr/0005-postgres-sampling.md` |
| 0006 — DuckDB's PostgreSQL extension ships in the wheel, and one script starts PostgreSQL for tests | unknown | `docs/adr/0006-postgres-extension-in-the-wheel.md` |
| 0007 — A PostgreSQL password lives only in memory and a temporary DuckDB secret, and every session is read-only | unknown | `docs/adr/0007-postgres-password-and-connection.md` |
| 0008 — A PostgreSQL password is kept in the OS credential store through keyring, and in memory only where there is none | unknown | `docs/adr/0008-postgres-password-in-the-os-credential-store.md` |
| 0009 — A PostgreSQL read is stopped by DuckDB's interrupt, then by the server's cancel; a full profile reads PostgreSQL's text through the scanner | unknown | `docs/adr/0009-stopping-a-postgres-read.md` |
| 0010 — How a profile reads a Parquet file: seeded slices, one in each eighth, and the row count from its metadata | unknown | `docs/adr/0010-parquet-sampling.md` |
