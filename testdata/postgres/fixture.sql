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
