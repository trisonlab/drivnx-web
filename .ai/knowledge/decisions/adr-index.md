---
title: Architecture decision index
status: CONFIRMED
generated_by: ai-skills knowledge discover
evidence:
- path: docs/adr/0001-workspace-packaging-and-session-token.md
  sha256: c7aefa50fb13bd077a656403b33297704d19b0f350ccdecf82f1c7b4156289a6
- path: docs/adr/0002-workspace-storage-sqlite-only.md
  sha256: 37710fce239923920c0c99664c13b07c646286cd0a6d9f60be5c62b4660e5283
- path: docs/adr/0003-contract-schema-native-with-odcs-export.md
  sha256: 7e66661935fb8164735a40415271532c2b582a649019ce6877815c47bca0954e
- path: docs/adr/0004-profile-values-and-caching.md
  sha256: 12322e4c09166082db9f7d0aaf9f2778771e08f1f3d831f9d0349ef795516980
- path: docs/adr/0005-postgres-sampling.md
  sha256: adb06a3e3e83e0021c8fc0a63117e726441e3d2eeaea5e54d2dcc4634c8dda88
- path: docs/adr/0006-postgres-extension-in-the-wheel.md
  sha256: 86662d0b293d67401493b0e41990665e64b609596d0533bd23d1a74b06bbab7b
- path: docs/adr/0007-postgres-password-and-connection.md
  sha256: 42718f21827289099afb04707938960474afb2b22bc9b2362b9532bcaf9a4ce4
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
