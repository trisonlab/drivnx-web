# 0007 — A PostgreSQL password lives only in memory and a temporary DuckDB secret, and every session is read-only

- **Status:** Accepted 2026-11-14
- **Date:** 2026-11-14
- **Source:** The plan for ticket DRIVN-47, approved by the user in chat on 2026-11-13; spec `CFX-APP-postgres`
  revision 2 (AC-01, AC-02, AC-03, AC-05, AC-14, AC-30); Section 7 of the direction doc; ADR 0006

## Context

A user connects PostgreSQL by host, port, database, user, password and TLS mode (AC-01). The direction doc's controls
are "Read-only credentials · no writes · bounded queries · cancellation" and "OS credential store · never in SQLite,
URLs, browser storage, logs or exports". The OS credential store comes with DRIVN-48; until then, AC-05 keeps the
password until DrivnX stops and asks for it again after a restart.

DrivnX reaches PostgreSQL through DuckDB's `postgres_scanner` (ADR 0006), which takes a libpq connection string. The
planning probe, against the fixture with DuckDB 2.0.0, found that **a password in that string comes back in DuckDB's
error text**: for a wrong password, a refused port, an unknown host, a missing database and TLS required alike.

## Decision

**1. The password is in two places only:** a dictionary in the API process's memory, keyed by source
(`KeptPasswords` in `local_api/postgres_api.py`), and a `TEMPORARY` DuckDB secret in the in-memory DuckDB of the one
job that connects. The secret goes when that connection closes. The password is never in SQLite, the connection string,
an environment variable, a file, a log, a URL or an answer from the API; the request models hold it as `SecretStr`. A
source in the workspace has no password column. DRIVN-48 puts the credential store behind `KeptPasswords`.

**2. The connection string holds no secret.** `attach()` in `connectors/postgres.py` creates the secret with the host,
port, database, user and password, and attaches with `sslmode`, `sslrootcert` (only for `verify-full`),
`connect_timeout`, `application_name=DrivnX` and `options=-c default_transaction_read_only=on`. Every value is quoted
for both SQL and libpq.

**3. Every session is read-only twice:** DuckDB attaches `READ_ONLY`, and the server starts each transaction read-only.
Either alone makes the server refuse a write, even through `postgres_execute`; each has its own test (AC-14).

**4. Failures are DrivnX's own words.** DuckDB's text is never re-raised, logged or returned. The reason comes from
libpq's last attempt only, as `prefer` tries again without TLS and the first attempt's error would mislead. Each AC-03
case (unreachable, refused credentials, no database, TLS) and a missing or wrong CA has its message; anything else gets
"DrivnX couldn't connect to this database."

**5. Nothing can become a connection string.** The API takes only its named fields (`extra="forbid"`), a host matching
`[A-Za-z0-9._:-]`, names without control characters, and a password of valid text with no NUL. A refusal never repeats
the input. A connection is tested before it's saved, so one that fails is never saved.

**6. The connect timeout is 10 seconds, as 5 per attempt,** because DuckDB connects a second time when the first
attempt fails (Measured).

## Alternatives considered

- **The password in the connection string:** DuckDB's errors repeat it, so every error path would need scrubbing, and a
  missed one leaks it. Rejected by the probe.
- **`PGPASSWORD`:** shared by the whole process and inherited by child processes. Rejected in planning.
- **A `passfile`:** a file on disk holding the password, which the spec forbids. Rejected in planning.
- **The password in SQLite, encrypted:** the key would have to live somewhere, and the spec says never in SQLite. The
  credential store (DRIVN-48) is the place.
- **Only one read-only layer:** each alone holds, but a DuckDB change or a server setting could undo one. Both are
  cheap, so both stay.

## Measured

2026-11-13 and 2026-11-14, DuckDB 2.0.0, against the fixture (PostgreSQL 18 here):

- With the password in the connection string, DuckDB's error text contained it in each failure tried. Given through a
  temporary secret, it was in none, and `duckdb_secrets()` shows `password=redacted`.
- DuckDB's `ATTACH` connects a second time whenever the first attempt fails, with the password inline or in a secret,
  whatever its pool settings (`pg_pool_max_connections`, `pg_connection_cache`, `pg_pool_acquire_mode`, the health
  check). `psql` with the same settings connects once. A server that never answers took 20 s with
  `connect_timeout=10`; with 5 s an attempt it fails at 10.1 s (three runs). `postgres_scan`, which might have
  connected once, isn't in DuckDB 2.0.
- A lone surrogate in the password makes DuckDB raise `RuntimeError('std::bad_cast')`, not a `duckdb.Error`, and a NUL
  is accepted but cuts the password at libpq. Neither leaked it; the API refuses both.
- The tests plant passwords and scan `workspace.db`, its WAL and the logs at trace level for them, for the right and a
  wrong password (`tests/integration/test_postgres_sources_api.py`).

## Consequences

- **A wrong password is two failed sign-ins** in the server's log, so an account lockout counts each attempt twice.
- After a restart, a database source asks for its password before anything is read (AC-05), until DRIVN-48.
- Failure reasons are read from libpq's English messages. A server or client set to another language gets the general
  message, never DuckDB's text.
- libpq reads `~/.pgpass` only when no password is given, and the form requires one. Under `require`, libpq's
  documentation says it also uses a `~/.postgresql/root.crt` or client certificate if one exists, so such a machine
  verifies more than `require` asks. This is from the documentation, not tested.
- The password stays in Python's memory until DrivnX stops; Python can't reliably wipe a string.
- Browser storage and exports aren't scanned yet: the UI keeps the password only in the form's state, and AC-30's scan
  comes with DRIVN-48.
