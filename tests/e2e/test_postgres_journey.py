"""The whole PostgreSQL journey in the browser, against the installed wheel and the fixture,
DRIVN-45 AC-35, and what it leaves behind, AC-29 and AC-15 (DRIVN-58)."""

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from .. import postgres_fixture
from ..conftest import StartDrivnX
from ..integration.test_credential_store import NO_STORE
from .test_accessibility import violations
from .test_connect_csv import details_of, open_set_up_app
from .test_connect_postgres import database_form, fill_in
from .test_profile_journey import (
    MASKED_EMAIL,
    PLANTED,
    assert_no_value_kept_or_logged,
    every_file_in,
)

SCHEMA = "drivnx_e2e_journey"
WRITER = "drivnx_e2e_writer"
WRITER_PASSWORD = "drivnx-e2e-writer"  # pragma: allowlist secret, ai-skills: allow
ROWS = 12
# Distinctive values in every text and number column, each matching the CSV journey's PLANTED,
# which no schema, object or column name here does.
EMAILS = [f"zq{row}k@planted.example" for row in range(1, ROWS + 1)]
NAMES = ["Zephyrine", "Quillmarsh", "Norvale", "Brindlewick"]
CODES = [f"rq{row}vz" for row in range(1, ROWS + 1)]
PHONES = [9182736000 + row for row in range(1, ROWS + 1)]
AMOUNTS = [7293846000 + row for row in range(1, ROWS + 1)]
PLANTED_VALUES = [*EMAILS, *NAMES, *CODES, *map(str, PHONES), *map(str, AMOUNTS)]
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE", f"DROP ROLE IF EXISTS {WRITER}")
# Made as the superuser. The writer reads everything and may add rows to `orders`, so the write
# warning shows (DRIVN-45 AC-16), and DrivnX could write if it ever tried.
OBJECTS = (
    f"CREATE ROLE {WRITER} LOGIN PASSWORD '{WRITER_PASSWORD}'",
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.contacts (id integer PRIMARY KEY, email text, name text, phone bigint)",
    f"INSERT INTO {SCHEMA}.contacts VALUES "
    + ", ".join(
        f"({row}, '{EMAILS[row - 1]}', '{NAMES[row % len(NAMES)]}', {PHONES[row - 1]})"
        for row in range(1, ROWS + 1)
    ),
    f"ANALYZE {SCHEMA}.contacts",
    f"CREATE TABLE {SCHEMA}.orders (code text, amount bigint)",
    f"INSERT INTO {SCHEMA}.orders VALUES "
    + ", ".join(f"('{CODES[row]}', {AMOUNTS[row]})" for row in range(ROWS)),
    f"ANALYZE {SCHEMA}.orders",
    f"CREATE VIEW {SCHEMA}.contact_names AS SELECT name FROM {SCHEMA}.contacts",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO {WRITER}",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {WRITER}",
    f"GRANT INSERT ON {SCHEMA}.orders TO {WRITER}",
)
CONTACTS = f"{SCHEMA}.contacts"
ORDERS = f"{SCHEMA}.orders"


@pytest.fixture
def journey_objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (*DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        for statement in DROP:
            postgres_admin(statement)


def walk_the_database_journey(page: Page, at_each_state: Callable[[], None]) -> None:
    """DRIVN-45 AC-35's journey, with the preview and values on request AC-29 adds, calling
    `at_each_state` in each state it reaches."""
    fill_in(page, WRITER_PASSWORD, WRITER)
    at_each_state()

    database_form(page).get_by_role("button", name="Save").click()
    listed = details_of(page, postgres_fixture.DATABASE).get_by_role(
        "region", name="Schemas and tables"
    )
    expect(listed.get_by_role("group", name=SCHEMA)).to_be_visible()
    at_each_state()

    listed.get_by_role("checkbox", name=f"Choose all of {SCHEMA}").click()
    listed.get_by_role("button", name="Save selection").click()
    expect(listed.get_by_role("status")).to_have_text("Selection saved.")
    at_each_state()

    listed.get_by_role("button", name="contacts").click()
    preview = page.get_by_role("region", name=CONTACTS).get_by_role("region", name="Preview")
    expect(preview).to_contain_text(EMAILS[0])
    at_each_state()

    profile = page.get_by_role("tabpanel", name="Profile")
    profile.get_by_role("button", name="Fast profile").click()
    warning = profile.get_by_role(
        "alertdialog", name=f"{WRITER} can change some of the chosen objects. Profile them anyway?"
    )
    expect(warning).to_be_visible()
    at_each_state()

    warning.get_by_role("button", name="Profile anyway").click()
    expect(
        profile.get_by_role("table", name=f"{CONTACTS}: the whole object, {ROWS} rows")
    ).to_be_visible()
    at_each_state()

    profile.get_by_role("button", name="Full profile").click()
    expect(profile.get_by_role("table", name=f"{CONTACTS}: every row, {ROWS} rows")).to_be_visible()
    expect(page.get_by_text("Profile status: Completed")).to_be_visible()
    at_each_state()

    findings = page.get_by_role("complementary", name="Intelligence & Review").get_by_role(
        "region", name="Findings"
    )
    identifier = findings.get_by_role("article").filter(
        has_text=f"Observed in {ORDERS}: code has {ROWS} distinct values (100%)."
    )
    identifier.get_by_role("button", name="Show the evidence").click()
    orders = profile.get_by_role("table", name=f"{ORDERS}: every row, {ROWS} rows")
    code = orders.get_by_role("row").filter(
        has=page.get_by_role("rowheader", name="code", exact=True)
    )
    expect(code.locator(":focus")).to_have_text(f"{ROWS} (100%)")
    at_each_state()

    # `code` is no one's personal data, so it asks nothing first; `name` and `phone` would.
    profile.get_by_role("button", name=f"Show values of code in {ORDERS}").click()
    codes = profile.get_by_role("region", name=f"Values in code of {ORDERS}")
    expect(codes).to_contain_text(
        f"These are the same rows as this run's only if {ORDERS} hasn't changed since this run."
    )
    expect(codes.get_by_role("table", name="Most frequent values")).to_contain_text(CODES[0])
    at_each_state()

    profile.get_by_role("button", name=f"Show values of email in {CONTACTS}").click()
    asking = profile.get_by_role(
        "alertdialog", name="This column may hold personal data. Show its values?"
    )
    expect(asking).to_be_visible()
    at_each_state()

    asking.get_by_role("button", name="Show values").click()
    emails = profile.get_by_role("region", name=f"Values in email of {CONTACTS}")
    expect(emails.get_by_role("table", name="Most frequent values")).to_contain_text(EMAILS[0])
    at_each_state()


def refused_for_the_write_warning(text: str, url: str) -> bool:
    """The browser's own line for the start the server refuses so the write warning shows
    (DRIVN-45 AC-16): a refusal by design, which the browser logs as it does any 409."""
    return text.endswith("status of 409 (Conflict)") and urlsplit(url).path == "/api/profile-runs"


def test_the_database_journey_on_the_installed_wheel_from_one_origin(
    start_drivnx: StartDrivnX, page: Page, journey_objects: None
) -> None:
    # DRIVN-45/AC-35: "adding a connection, confirming the write warning, choosing objects,
    # running a fast profile, running a full profile and opening a finding's evidence passes in
    # Playwright from one origin, with an automated accessibility check on the new screens in each
    # state".
    assert [value for value in PLANTED_VALUES if not PLANTED.search(value.encode())] == []
    requested: list[str] = []
    console_errors: list[str] = []
    page.on("request", lambda request: requested.append(request.url))
    page.on(
        "console",
        lambda message: message.type == "error"
        and not refused_for_the_write_warning(message.text, message.location["url"])
        and console_errors.append(message.text),
    )
    url = start_drivnx("--no-browser", env=NO_STORE).wait_for_launch().url
    open_set_up_app(page, url)

    def no_violations() -> None:
        assert violations(page) == []

    walk_the_database_journey(page, no_violations)

    origin = urlsplit(url)
    assert origin.hostname == "127.0.0.1"
    assert {urlsplit(address).netloc for address in requested} == {origin.netloc}
    paths = [urlsplit(address).path for address in requested]
    assert any(path.startswith("/assets/") for path in paths)
    assert any(path.startswith("/api/") for path in paths)
    assert console_errors == []


# Where each of the server's log entries starts: the time `LOG_LINE_PREFIX` puts first. A
# statement on several lines goes on in lines that start with a tab.
ENTRY = re.compile(r"^(?=\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", re.MULTILINE)
# What the server logs for each statement a session sends, with `log_statement = 'all'`.
STATEMENT = re.compile(r" LOG:  (?:statement|execute [^:]*): (.*)", re.DOTALL)
# Every form of statement DrivnX sent as the journey's role on the test's first run, 2026-11-22,
# none of which writes or changes the schema (DRIVN-45 AC-15). A statement of any other form, or
# holding what can make a read write (`WRITES_THOUGH_READ`), fails the test by name. These are
# the forms seen, not a parse of SQL: what a function a read calls does isn't checked here, and
# the session is read-only, so the server refuses one that writes (AC-14).
READ_ONLY = [
    re.compile(form)
    for form in (
        # The scanner's session: each read in a read-only transaction, and the pool's checks.
        r"BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        r"COMMIT",
        r"ROLLBACK",
        r"DISCARD ALL",
        r"SELECT 1",
        r"SELECT version\(\), \(SELECT COUNT\(\*\) FROM pg_settings WHERE name LIKE 'rds%'\)",
        r"SELECT to_regclass\('duckdb_secrets'\)",
        # The scanner's catalog, read when it attaches.
        r"SELECT [^;]+ FROM (pg_catalog\.)?pg_(namespace|class|enum|type|indexes)\b[^;]*",
        # Every read of DrivnX's, through `postgres_query` or the scanner.
        r'COPY \(SELECT [^;]+\) TO STDOUT \(FORMAT "binary"\)',
    )
]


def entries_of(log: str, role: str) -> list[str]:
    """The role's entries in the server's log, each whole."""
    return [entry for entry in ENTRY.split(log) if f" user={role} " in entry.partition("\n")[0]]


# A `;` outside a literal: an even number of quotes after it, as `''` in a literal is two.
BETWEEN_STATEMENTS = re.compile(r";(?=(?:[^']*'[^']*')*[^']*$)")


def statements_in(entries: list[str]) -> list[str]:
    """Each statement the entries log, on one line. A message may hold several, as the scanner's
    catalog does."""
    return [
        " ".join(statement.split())
        for entry in entries
        if (found := STATEMENT.search(entry))
        for statement in BETWEEN_STATEMENTS.split(found.group(1))
        if statement.strip()
    ]


# What can make a statement of a read's form write: `SELECT … INTO` makes a table, and a row
# lock is refused in a read-only transaction as a write is.
WRITES_THOUGH_READ = re.compile(
    r"\bINTO\b|\bFOR\s+(NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)\b", re.IGNORECASE
)


def not_read_only(statements: list[str]) -> list[str]:
    return [
        statement
        for statement in statements
        if WRITES_THOUGH_READ.search(statement)
        or not any(form.fullmatch(statement) for form in READ_ONLY)
    ]


def not_drivnxs_reads(log: str, role: str) -> list[str]:
    """Everything in the log that isn't shown to be one of DrivnX's reads as the role (AC-15),
    each entry whole: one without the test's prefix, which names no role, as when the prefix was
    put back mid-run; the role's from another application; and each statement of the role's of
    no form in `READ_ONLY`, or of one that can write."""
    entries = entries_of(log, role)
    return [
        *(entry for entry in ENTRY.split(log) if entry and " app=" not in entry.partition("\n")[0]),
        *(entry for entry in entries if " app=DrivnX " not in entry.partition("\n")[0]),
        *not_read_only(statements_in(entries)),
    ]


def test_the_database_journey_keeps_no_value_and_sends_only_drivnxs_reads(
    start_drivnx: StartDrivnX,
    page: Page,
    journey_objects: None,
    logging_statements: Callable[[str], None],
    tmp_path: Path,
) -> None:
    # DRIVN-45/AC-29: "a full journey with distinctive planted values in the fixture, including
    # the preview and values on request, then a scan of every file in the workspace, during the
    # run and after DrivnX stops, and of its logs at their most detailed finds no value from the
    # database, except masked patterns and the names of schemas, objects, columns and the
    # connection". DRIVN-45/AC-15: "given the fixture's statement log, when the full journey
    # runs, then DrivnX sent no statement that writes or changes the schema, and every statement
    # carries application_name DrivnX".
    logging_statements(WRITER)
    server_log = postgres_fixture.server_log_from_now()
    workspace = tmp_path / "workspace"
    drivnx = start_drivnx("--no-browser", "--log-level", "trace", workspace=workspace, env=NO_STORE)
    open_set_up_app(page, drivnx.wait_for_launch().url)

    walk_the_database_journey(page, lambda: None)

    # The run saved `email`'s masked pattern, so a scan of the wrong files can't pass.
    assert MASKED_EMAIL in every_file_in(workspace)
    assert_no_value_kept_or_logged(drivnx, workspace, PLANTED_VALUES)
    logged = server_log()
    assert len(statements_in(entries_of(logged, WRITER))) > 10  # so another role's log can't pass
    assert not_drivnxs_reads(logged, WRITER) == []


def logged(*lines: str, prefix: str = f"app=DrivnX user={WRITER} ") -> str:
    """A server log of the lines given, each with the prefix the test sets."""
    return "".join(f"2026-11-23 10:00:00.000 UTC [4242] {prefix}LOG:  {line}\n" for line in lines)


def test_drivnxs_reads_as_the_server_logs_them_are_read_only() -> None:
    # As the fixture logged them on the test's first run: a message of several statements, one
    # on several lines, and literals holding quotes and a backslash.
    log = logged(
        "statement: BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
        "\t\n\tSELECT oid, nspname\n\tFROM pg_namespace\n\t\n\tORDER BY oid;\n",
        "statement: COPY (SELECT \"nspname\"::VARCHAR FROM (SELECT n.nspname FROM pg_namespace n"
        " WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema')"
        ' AS __unnamed_subquery ) TO STDOUT (FORMAT "binary");',
        "statement: COPY (SELECT set_config('client_connection_check_interval', '1s', true))"
        ' TO STDOUT (FORMAT "binary");',
        "statement: COMMIT",
    )

    assert not_drivnxs_reads(log, WRITER) == []


def test_a_select_that_makes_a_table_isnt_a_read() -> None:
    # The review of DRIVN-58: the catalog's form took it in.
    made = "SELECT relname INTO stolen FROM pg_class"

    assert not_drivnxs_reads(logged(f"statement: {made}"), WRITER) == [made]


@pytest.mark.parametrize("lock", ["FOR UPDATE", "for no key update", "FOR SHARE", "FOR KEY SHARE"])
def test_a_select_that_locks_rows_isnt_a_read(lock: str) -> None:
    # The review of DRIVN-58: PostgreSQL refuses each in a read-only transaction, as a write.
    locking = f"SELECT relname FROM pg_class {lock}"

    assert not_drivnxs_reads(logged(f"statement: {locking}"), WRITER) == [locking]


def test_a_semicolon_in_a_literal_doesnt_split_a_write_into_reads() -> None:
    # The review of DRIVN-58: split at the `;` in its literal, a COPY to the server's file was
    # two parts, each of a read's form.
    to_a_file = (
        "COPY (SELECT ') TO STDOUT (FORMAT \"binary\");SELECT x FROM pg_class WHERE y = '"
        " AS a) TO '/tmp/x'"
    )

    assert not_drivnxs_reads(logged(f"statement: {to_a_file}"), WRITER) == [to_a_file]


def test_an_entry_without_the_tests_prefix_cant_be_checked_so_it_fails() -> None:
    # The review of DRIVN-58: with the prefix put back mid-run, as by another checkout's test on
    # a shared fixture, an entry names no role, so a write in it was skipped.
    unprefixed = "2026-11-23 10:00:01.000 UTC [4242] LOG:  statement: INSERT INTO t VALUES (1)\n"

    assert not_drivnxs_reads(logged("statement: SELECT 1") + unprefixed, WRITER) == [unprefixed]
