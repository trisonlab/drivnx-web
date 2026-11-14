-- The PostgreSQL fixture's database (CFX-APP-postgres AC-34). Hand-written, and every value in it
-- is invented. Run once by a superuser: the Compose image runs it at start-up, and
-- tests/postgres_fixture.py runs it on a server it started itself.

-- What DrivnX connects as in tests: it can read the fixture's tables and change nothing.
CREATE ROLE drivnx_fixture LOGIN
  PASSWORD 'drivnx-fixture';  -- pragma: allowlist secret, ai-skills: allow
CREATE DATABASE drivnx_fixture;
\connect drivnx_fixture

CREATE TABLE people (id integer PRIMARY KEY, name text NOT NULL, joined date);
INSERT INTO people VALUES (1, 'Ann', '2024-01-05'), (2, 'Bob', NULL), (3, 'Zoé', '2024-03-17');
GRANT SELECT ON people TO drivnx_fixture;

-- The same, as an account pg_hba.conf signs in only without TLS (DRIVN-47).
CREATE ROLE drivnx_plain LOGIN
  PASSWORD 'drivnx-plain';  -- pragma: allowlist secret, ai-skills: allow
GRANT SELECT ON people TO drivnx_plain;

-- A password with a quote, a backslash, a space and an accent, which must reach the server as typed.
CREATE ROLE drivnx_quotes LOGIN
  PASSWORD 'it''s a \ test é';  -- pragma: allowlist secret, ai-skills: allow
