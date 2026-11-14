# 0006 — DuckDB's PostgreSQL extension ships in the wheel, and one script starts PostgreSQL for tests

- **Status:** Accepted 2026-11-13
- **Date:** 2026-11-13
- **Source:** The user's decisions in chat on 2026-11-13, recorded in spec `CFX-APP-postgres` (revision 2: "the PostgreSQL
  extension is bundled in the wheel") and in ticket DRIVN-46's plan; ADR 0005; the CI rules in `tests/integration/test_ci.py`

## Context

DrivnX reads PostgreSQL through DuckDB's `postgres_scanner` extension (Section 5 of the direction doc, ADR 0005). The
`duckdb` package doesn't include it, and DuckDB would download it on first use, but DrivnX turns that off: "Nothing may leave
the machine or arrive from outside it" (`packages/connectors/csv_files.py`). An extension only loads into the DuckDB version
it was built for, and it's a native binary per platform.

Tests also need a real PostgreSQL server on Linux, macOS and Windows. GitHub's macOS runner has neither Docker nor
PostgreSQL, its Windows runner has PostgreSQL 17 but can't run Linux images, and CI's steps may not test the platform
(first slice AC-53, enforced by `test_ci.py`).

## Decision

**1. One wheel carries the extension for every tested platform:** `linux_amd64`, `osx_arm64` and `windows_amd64`. The wheel
build fetches them from DuckDB's repository into `packages/connectors/postgres_extension/`, which git ignores, and the wheel
names it as an artifact. An editable install (`uv sync`) fetches only this machine's, if it's missing, so tests and `uv run`
can load it.

**2. What's fetched is pinned.** `hatch_postgres.py` holds the DuckDB version and the sha256 of each platform's `.gz`, and a
download that doesn't match stops the build before anything is written. A build whose `uv.lock` has another DuckDB stops too,
saying to pin the new version's hashes. `pyproject.toml` pins `duckdb==2.0.0`. `hatch_postgres.py` uses only the standard
library, so the build hook and the tests load it by path; it isn't shipped.

**3. DrivnX loads the extension by path,** never by name, which DuckDB would look up in its own folder and repository. DuckDB's
signature check stays on: `allow_unsigned_extensions` is never set. On a platform with nothing bundled, `load_postgres` raises
`PostgresUnavailable` naming the platform, for the screen to say PostgreSQL isn't available there (DRIVN-47).

**4. One script starts the PostgreSQL fixture,** `tests/postgres_fixture.py start|stop`, locally and in CI. Where Docker has
Compose and runs Linux containers, it's `testdata/postgres/compose.yaml`: PostgreSQL 18.7 pinned by digest, on
`127.0.0.1:54329`. Elsewhere it starts a server of its own in the temporary folder, from `PGBIN` (set on GitHub's Windows
runner, PostgreSQL 17), else Homebrew's `postgresql@17`, installed if missing, else a `pg_ctl` on the path. Both load the
hand-written, synthetic `testdata/postgres/fixture.sql` and use password sign-in over TCP only. This script is the one place
setup depends on the platform; the workflow still has none. Since DRIVN-47 both serve TLS, with a throwaway CA and server
certificate that `start` makes with `openssl`, and read one committed `testdata/postgres/pg_hba.conf`, which refuses the
`drivnx_plain` account over TLS, so an unencrypted connection and TLS that is required and fails can be tested
too.

**5. CI requires the fixture.** The test job sets `DRIVNX_REQUIRE_POSTGRES=1`, so a test needing PostgreSQL fails rather than
skips when the server didn't start. Locally, it skips and names the command to run.

## Alternatives considered

- **A wheel per platform:** about a third of the size, but a release needs a build on each OS and the build hook has to set
  the platform tag, ending "one build command for a release" (`hatch_build.py`). Rejected by the user.
- **Download on first use, after asking:** the least build work, but it breaks the no-download rule and fails on networks
  that block DuckDB's host. Rejected when the spec was approved.
- **psycopg instead of the extension:** a normal dependency, but a second way to read data, which the direction doc avoids.
  Rejected when the spec was approved.
- **Committing the binaries:** about 157 MB in git, growing with every DuckDB upgrade.
- **Setting up PostgreSQL per OS in CI's steps:** forbidden by `test_ci.py`, as a platform test in a step could skip a check.
- **A Linux-only PostgreSQL job:** simplest, but attaching a database would go untested on macOS and Windows. Rejected by the
  user.
- **A pip-installed PostgreSQL in every OS:** one version everywhere, but it replaces the Compose fixture the spec names and
  trusts a third party's build of PostgreSQL. Rejected by the user.

## Measured

2026-11-13, DuckDB 2.0.0, extension version `c0f5a3c0c4`.

| | `.gz` downloaded | Unpacked |
|---|---|---|
| `linux_amd64` | 18,910,802 bytes | 59,211,910 bytes |
| `osx_arm64` | 14,351,576 bytes | 45,908,758 bytes |
| `windows_amd64` | 16,928,583 bytes | 52,114,454 bytes |

- The wheel is 50.3 MB, against 0.16 MB for the rest of it, and 157.7 MB installed, against 0.5 MB.
- Five tests including one wheel build with the three downloads took 12.1 s here; the build wasn't timed alone.
- DuckDB's repository answers Python's default User-Agent (`Python-urllib/3.12`) with HTTP 403 and other ones with 200
  (checked with curl), so the build sends a neutral `User-Agent: build`.
- Starting the fixture's own server took about 1 s here.
- Ticket DRIVN-46 records the sampling measurement on the fixture: every decision of ADR 0005 held.

## Consequences

- Every wheel build downloads about 50 MB, and needs `extensions.duckdb.org`.
- Upgrading DuckDB means pinning the new extension's three hashes in `hatch_postgres.py`; the build says so if they're missing.
- A machine on another platform, such as Linux on ARM or an Intel Mac, can't use PostgreSQL, and is told so.
- Tests run on two PostgreSQL versions: 18.7 through Compose on Linux, and 17 from the runner or Homebrew on macOS and Windows.
  Only these are claimed as tested.
- A developer needs Docker with Compose, or a PostgreSQL install, to run the PostgreSQL tests; without one they skip.
  They also need `openssl` on the path, for the fixture's certificates (DRIVN-47).
- The fixture's passwords are test values for a server on loopback only. Each line carries the markers both secret scanners
  document (`pragma: allowlist secret`, `ai-skills: allow`).
