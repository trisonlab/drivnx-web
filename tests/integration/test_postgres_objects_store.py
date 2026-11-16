"""Saving a database's objects and the user's choice of them, CFX-APP-postgres AC-08 to AC-11 and
AC-13 (DRIVN-49). No PostgreSQL here: what discovery found is written by hand."""

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from artifacts.schema import read_revision
from artifacts.sources import (
    NotChoosable,
    ObjectFound,
    StoredColumn,
    StoredKey,
    StoredObject,
    StoredPostgresSource,
    add_postgres_source,
    read_object,
    read_objects,
    save_discovery,
    save_selection,
)
from artifacts.workspace import MIGRATIONS_DIR, Workspace, open_workspace
from sqlalchemy import URL, create_engine
from sqlalchemy.pool import NullPool

SOURCE = StoredPostgresSource(
    id="a" * 32,
    host="127.0.0.1",
    port=54329,
    database="drivnx_fixture",
    user="drivnx_fixture",
    tls_mode="prefer",
    ca_path=None,
)
ID = StoredColumn("id", "bigint", "integer", allows_nulls=False)
REGION = StoredColumn("region", "text", "string", allows_nulls=True)
ORDERS = ObjectFound(
    schema="sales",
    name="orders",
    kind="table",
    estimated_rows=1200,
    readable=True,
    columns=(ID, REGION, StoredColumn("amount", "numeric(10,2)", "number", allows_nulls=True)),
    keys=(
        StoredKey("primary_key", "orders_key", ("id",)),
        StoredKey("unique", "orders, odd", ("region", "id")),
        StoredKey(
            "foreign_key",
            "orders_region",
            ("region",),
            referenced_schema="ref",
            referenced_object="regions",
            referenced_columns=("name",),
        ),
    ),
)
EVENTS = ObjectFound("sales", "events", "partitioned_table", None, True, (ID,), ())
ORDER_VIEW = ObjectFound("sales", "order_view", "view", None, True, (ID, REGION), ())
TOTALS = ObjectFound("audit", "Odd.Name, é", "materialized_view", 2, True, (REGION,), ())
SECRET = ObjectFound("audit", "secret", "table", 0, False, (ID,), ())
SCHEMAS = ("sales", "empty", "audit")
FOUND = (ORDERS, EVENTS, ORDER_VIEW, TOTALS, SECRET)

# A CSV source as the schema before 0008 stored it: corrected once, so with an earlier dataset,
# and a run that measured the current one.
BEFORE_OBJECTS = [
    "INSERT INTO source (id, kind, path, delimiter, encoding, has_header, size_bytes,"
    " modified_at, corrected) VALUES ('s1', 'csv', '/data/a.csv', ',', 'UTF-8', 1, 10,"
    " '2026-01-01T00:00:00+00:00', 1)",
    "INSERT INTO source (id, kind, host, port, database_name, user_name, tls_mode)"
    " VALUES ('p1', 'postgres', 'h', 5432, 'db', 'u', 'prefer')",
    "INSERT INTO dataset (id, source_id, name, replaced_at)"
    " VALUES ('d0', 's1', 'a.csv', '2026-01-02T00:00:00+00:00')",
    "INSERT INTO dataset (id, source_id, name) VALUES ('d1', 's1', 'a.csv')",
    "INSERT INTO field (id, dataset_id, position, name, physical_type, normalised_type)"
    " VALUES ('f1', 'd1', 0, 'id', 'BIGINT', 'integer')",
    "INSERT INTO profile_run (id, scope, mode, status, started_at)"
    " VALUES ('r1', '{\"dataset_id\": \"d1\"}', 'fast', 'completed', '2026-01-03T00:00:00+00:00')",
    "INSERT INTO observation (id, profile_run_id, dataset_id, field_id, metric, value, scope,"
    " method, precision) VALUES ('o1', 'r1', 'd1', 'f1', 'null_count', '0', 'sample', 'count',"
    " 'exact')",
]
TABLES = ("source", "dataset", "field", "profile_run", "observation")
NEW_COLUMNS = {
    "discovered_at",
    "schema_name",
    "object_name",
    "object_kind",
    "estimated_rows",
    "readable",
    "populated",
    "chosen",
    "allows_nulls",
}


def rows(database: Path, table: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        # Only the columns the schema before 0008 had, in their order.
        kept = [column for column in columns if column not in NEW_COLUMNS]
        return connection.execute(
            f"SELECT {', '.join(kept)} FROM {table} ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()


def test_a_workspace_at_0007_keeps_its_csv_sources_runs_and_one_current_dataset(
    tmp_path: Path,
) -> None:
    # CFX-APP-postgres/AC-08: migration 0008 adds what discovery saves to a workspace that
    # already has data, and a CSV source still has one current dataset.
    before = tmp_path / "migrations"
    shutil.copytree(
        MIGRATIONS_DIR, before, ignore=shutil.ignore_patterns("0008_*", "__pycache__")
    )
    directory = tmp_path / "workspace"
    open_workspace(directory, before).close()
    database = directory / "workspace.db"
    connection = sqlite3.connect(database)
    with connection:
        for statement in BEFORE_OBJECTS:
            connection.execute(statement)
    connection.close()
    expected = {table: rows(database, table) for table in TABLES}

    open_workspace(directory).close()

    assert read_revision(database) == "0008"
    assert {table: rows(database, table) for table in TABLES} == expected
    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO dataset (id, source_id, name) VALUES ('d2', 's1', 'a.csv')"
            )
    finally:
        connection.close()


def downgrade_to_0007(database: Path) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    engine = create_engine(URL.create("sqlite", database=str(database)), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0007")
    finally:
        engine.dispose()


def test_a_workspace_goes_back_to_0007_unless_it_has_a_databases_objects(
    tmp_path: Path,
) -> None:
    # CFX-APP-postgres/AC-08: the downgrade gives back the 0007 schema with every CSV row, and
    # refuses, changing nothing, once a dataset is a database's object (runs may refer to it).
    directory = tmp_path / "workspace"
    open_workspace(directory).close()
    database = directory / "workspace.db"
    connection = sqlite3.connect(database)
    with connection:
        for statement in BEFORE_OBJECTS:
            connection.execute(statement)
    connection.close()
    expected = {table: rows(database, table) for table in TABLES}

    downgrade_to_0007(database)

    assert read_revision(database) == "0007"
    assert {table: rows(database, table) for table in TABLES} == expected
    connection = sqlite3.connect(database)
    try:
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master")}
        assert not {"declared_key", "database_schema", "one_current_object"} & tables
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO dataset (id, source_id, name) VALUES ('d2', 's1', 'a.csv')"
            )
    finally:
        connection.close()

    open_workspace(directory).close()
    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            "INSERT INTO dataset (id, source_id, name, schema_name, object_name, object_kind,"
            " readable, populated) VALUES ('d3', 'p1', 's.t', 's', 't', 'table', 1, 1)"
        )
    connection.close()

    with pytest.raises(RuntimeError, match="stays at 0008"):
        downgrade_to_0007(database)
    assert read_revision(database) == "0008"


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A workspace with rows of each shape the code writes, which the database accepts."""
    directory = tmp_path / "workspace"
    open_workspace(directory).close()
    connection = sqlite3.connect(directory / "workspace.db")
    try:
        with connection:
            for statement in (
                "INSERT INTO source (id, kind, path, delimiter, encoding, has_header,"
                " size_bytes, modified_at) VALUES ('s1', 'csv', '/data/a.csv', ',', 'UTF-8', 1,"
                " 10, 't')",
                "INSERT INTO dataset (id, source_id, name) VALUES ('d0', 's1', 'a.csv')",
                "INSERT INTO field (id, dataset_id, position, name, physical_type,"
                " normalised_type) VALUES ('f0', 'd0', 0, 'id', 'BIGINT', 'integer')",
                "INSERT INTO source (id, kind, host, port, database_name, user_name, tls_mode)"
                " VALUES ('p1', 'postgres', 'h', 5432, 'db', 'u', 'prefer')",
                "INSERT INTO dataset (id, source_id, name, schema_name, object_name,"
                " object_kind, estimated_rows, readable, populated, chosen)"
                " VALUES ('d1', 'p1', 's.t', 's', 't', 'table', 0, 1, 1, 1)",
                "INSERT INTO dataset (id, source_id, name, schema_name, object_name,"
                " object_kind, readable, populated)"
                " VALUES ('d2', 'p1', 's.v', 's', 'v', 'view', 0, 1)",
                "INSERT INTO field (id, dataset_id, position, name, physical_type,"
                " normalised_type, allows_nulls) VALUES ('f1', 'd1', 0, 'id', 'integer',"
                " 'integer', 0)",
                "INSERT INTO declared_key (id, dataset_id, kind, name, columns)"
                " VALUES ('k0', 'd1', 'primary_key', 't_key', '[\"id\"]')",
                "INSERT INTO declared_key (id, dataset_id, kind, name, columns,"
                " referenced_schema, referenced_object, referenced_columns)"
                " VALUES ('k1', 'd1', 'foreign_key', 't_p', '[\"id\"]', 's', 'p', '[\"id\"]')",
            ):
                connection.execute(statement)
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize(
    "values",
    [
        # (schema_name, object_name, object_kind, readable, chosen)
        "('s', 'u', 'table', 0, 1)",
        "(NULL, NULL, NULL, NULL, 1)",
        "('s', 'u', 'table', NULL, 0)",
        "(NULL, NULL, NULL, 1, 0)",
        "('s', NULL, 'table', 1, 0)",
        "(NULL, 'u', 'table', 1, 0)",
        "('s', 'u', NULL, 1, 0)",
        "('s', 'u', 'foreign_table', 1, 0)",
    ],
)
def test_the_database_refuses_a_chosen_object_without_read_access(
    database: sqlite3.Connection, values: str
) -> None:
    # CFX-APP-postgres/AC-10: an object without read access can't be chosen, and an object row
    # always says what it is and whether it can be read.
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO dataset (id, source_id, name, schema_name, object_name, object_kind,"
            # Populated whenever it is an object, so only what each case names is wrong.
            f" readable, chosen, populated) SELECT 'd9', 'p1', 's.u', *,"
            f" CASE WHEN column3 IS NULL THEN NULL ELSE 1 END FROM (VALUES {values})"
        )


@pytest.mark.parametrize(
    "values",
    [
        # (kind, referenced_schema, referenced_object, referenced_columns)
        "('foreign_key', NULL, NULL, NULL)",
        "('foreign_key', 's', NULL, '[\"id\"]')",
        "('foreign_key', NULL, 'p', '[\"id\"]')",
        "('foreign_key', 's', 'p', NULL)",
        "('primary_key', 's', 'p', '[\"id\"]')",
        "('unique', NULL, NULL, '[\"id\"]')",
    ],
)
def test_the_database_refuses_a_foreign_key_without_its_target(
    database: sqlite3.Connection, values: str
) -> None:
    # CFX-APP-postgres/AC-09: a foreign key names the table and columns it references, and only
    # a foreign key does.
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO declared_key (id, dataset_id, name, columns, kind, referenced_schema,"
            " referenced_object, referenced_columns)"
            f" SELECT 'k9', 'd1', 'k', '[\"a\"]', * FROM (VALUES {values})"
        )


@pytest.mark.parametrize(
    "statement",
    [
        # The same object listed twice: a source has one current dataset per object.
        "INSERT INTO dataset (id, source_id, name, schema_name, object_name, object_kind,"
        " readable, populated) VALUES ('d9', 'p1', 's.t', 's', 't', 'view', 1, 1)",
        "INSERT INTO dataset (id, source_id, name, schema_name, object_name, object_kind,"
        " estimated_rows, readable, populated)"
        " VALUES ('d9', 'p1', 's.u', 's', 'u', 'table', -1, 1, 1)",
        "INSERT INTO field (id, dataset_id, position, name, physical_type, normalised_type,"
        " allows_nulls) VALUES ('f9', 'd1', 1, 'a', 'text', 'string', 2)",
        "INSERT INTO declared_key (id, dataset_id, kind, name, columns)"
        " VALUES ('k9', 'd1', 'check', 'k', '[\"id\"]')",
        "INSERT INTO declared_key (id, dataset_id, kind, name, columns)"
        " VALUES ('k9', 'd1', 'unique', 'k', 'id, a')",
    ],
    ids=["object twice", "negative estimate", "nullability", "key kind", "key columns"],
)
def test_the_database_refuses_what_discovery_never_finds(
    database: sqlite3.Connection, statement: str
) -> None:
    # CFX-APP-postgres/AC-08, AC-09: an object is listed once with an estimate that is a row
    # count; a column allows nulls or not; a key is a primary, unique or foreign key on a list of
    # column names.
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(statement)


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Workspace]:
    """A workspace with the database source saved, before any discovery."""
    workspace = open_workspace(tmp_path / "workspace")
    try:
        assert add_postgres_source(workspace.engine, SOURCE)
        yield workspace
    finally:
        workspace.close()


def summary(found: ObjectFound) -> tuple[object, ...]:
    return (found.schema, found.name, found.kind, found.estimated_rows, found.readable)


def listed(workspace: Workspace) -> list[tuple[object, ...]]:
    return [
        (stored.schema, stored.name, stored.kind, stored.estimated_rows, stored.readable)
        for stored in read_objects(workspace.engine, SOURCE.id).objects
    ]


def test_saving_a_discovery_lists_schemas_objects_and_kinds_in_order(
    workspace: Workspace,
) -> None:
    # CFX-APP-postgres/AC-08: every schema, an empty one too, with its objects, each with its
    # kind and estimated row count, by schema then name. Nothing is chosen yet.
    assert read_objects(workspace.engine, SOURCE.id).discovered_at is None

    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)

    stored = read_objects(workspace.engine, SOURCE.id)
    assert stored.discovered_at is not None
    assert stored.schemas == ("audit", "empty", "sales")
    assert listed(workspace) == [
        summary(TOTALS),
        summary(SECRET),
        summary(EVENTS),
        summary(ORDER_VIEW),
        summary(ORDERS),
    ]
    assert not [found for found in stored.objects if found.chosen]
    assert len({found.id for found in stored.objects}) == len(FOUND)


def object_id(workspace: Workspace, schema: str, name: str) -> str:
    (found,) = [
        stored
        for stored in read_objects(workspace.engine, SOURCE.id).objects
        if (stored.schema, stored.name) == (schema, name)
    ]
    return found.id


def test_an_opened_object_has_its_columns_and_declared_keys(workspace: Workspace) -> None:
    # CFX-APP-postgres/AC-09: columns in order with their PostgreSQL type, normalised type and
    # whether they allow nulls; keys with their columns in key order and what a foreign key
    # references. Only an object of this source opens.
    other = StoredPostgresSource(**{**SOURCE.__dict__, "id": "b" * 32, "user": "someone"})
    assert add_postgres_source(workspace.engine, other)
    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)
    orders = object_id(workspace, "sales", "orders")

    opened = read_object(workspace.engine, SOURCE.id, orders)

    assert opened is not None
    stored, columns, keys = opened
    assert summary(ORDERS) == (
        stored.schema, stored.name, stored.kind, stored.estimated_rows, stored.readable
    )
    assert (stored.id, stored.chosen) == (orders, False)
    assert columns == ORDERS.columns
    assert keys == ORDERS.keys
    assert read_object(workspace.engine, SOURCE.id, object_id(workspace, "sales", "events")) == (
        read_objects(workspace.engine, SOURCE.id).objects[2],
        (ID,),
        (),
    )
    assert read_object(workspace.engine, other.id, orders) is None
    assert read_object(workspace.engine, SOURCE.id, "no-such-object") is None


def chosen(workspace: Workspace) -> set[tuple[str, str]]:
    return {
        (stored.schema, stored.name)
        for stored in read_objects(workspace.engine, SOURCE.id).objects
        if stored.chosen
    }


def test_a_selection_stays_after_reopening_the_workspace(tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-11: the chosen objects are saved, and saving again replaces them.
    directory = tmp_path / "workspace"
    workspace = open_workspace(directory)
    try:
        assert add_postgres_source(workspace.engine, SOURCE)
        save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)
        ids = {
            object_id(workspace, "sales", "orders"),
            object_id(workspace, "audit", "Odd.Name, é"),
        }
        save_selection(workspace.engine, SOURCE.id, frozenset(ids))
    finally:
        workspace.close()

    workspace = open_workspace(directory)
    try:
        assert chosen(workspace) == {("sales", "orders"), ("audit", "Odd.Name, é")}

        save_selection(
            workspace.engine, SOURCE.id, frozenset({object_id(workspace, "sales", "events")})
        )

        assert chosen(workspace) == {("sales", "events")}
    finally:
        workspace.close()


def test_choosing_nothing_before_any_discovery_saves_nothing(workspace: Workspace) -> None:
    # CFX-APP-postgres/AC-11: an empty selection is a selection, even with nothing listed.
    save_selection(workspace.engine, SOURCE.id, frozenset())

    with pytest.raises(NotChoosable):
        save_selection(workspace.engine, SOURCE.id, frozenset({"f" * 32}))
    assert read_objects(workspace.engine, SOURCE.id).objects == ()


@pytest.mark.parametrize("refused", ["no read access", "another source's", "unknown"])
def test_choosing_an_object_without_read_access_or_from_another_source_changes_nothing(
    workspace: Workspace, refused: str
) -> None:
    # CFX-APP-postgres/AC-10, AC-11: only this source's objects that the user can read can be
    # chosen, and a selection with any other id is refused whole.
    other = StoredPostgresSource(**{**SOURCE.__dict__, "id": "b" * 32, "user": "someone"})
    assert add_postgres_source(workspace.engine, other)
    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)
    save_discovery(workspace.engine, other.id, SCHEMAS, FOUND)
    orders = object_id(workspace, "sales", "orders")
    save_selection(workspace.engine, SOURCE.id, frozenset({orders}))
    bad = {
        "no read access": object_id(workspace, "audit", "secret"),
        "another source's": read_objects(workspace.engine, other.id).objects[0].id,
        "unknown": "f" * 32,
    }[refused]

    with pytest.raises(NotChoosable):
        save_selection(
            workspace.engine,
            SOURCE.id,
            frozenset({object_id(workspace, "sales", "events"), bad}),
        )

    assert chosen(workspace) == {("sales", "orders")}
    assert not [found for found in read_objects(workspace.engine, other.id).objects if found.chosen]


def dataset_row(workspace: Workspace, dataset_id: str) -> tuple[object, ...]:
    """A dataset as earlier runs see it, replaced or not, with its fields' names."""
    connection = sqlite3.connect(workspace.directory / "workspace.db")
    try:
        replaced_at, name = connection.execute(
            "SELECT replaced_at, name FROM dataset WHERE id = ?", (dataset_id,)
        ).fetchone()
        fields = connection.execute(
            "SELECT name FROM field WHERE dataset_id = ? ORDER BY position", (dataset_id,)
        ).fetchall()
        return (replaced_at is not None, name, [field for (field,) in fields])
    finally:
        connection.close()


def test_a_refresh_after_a_drop_no_longer_lists_it_and_keeps_its_dataset_for_earlier_runs(
    workspace: Workspace,
) -> None:
    # CFX-APP-postgres/AC-13: the list shows the database as it is now. A dropped object, or
    # schema, is gone; its dataset stays, replaced, so what a run measured still points at it.
    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)
    view = object_id(workspace, "sales", "order_view")
    orders = object_id(workspace, "sales", "orders")
    first = read_objects(workspace.engine, SOURCE.id).discovered_at

    save_discovery(
        workspace.engine, SOURCE.id, ("sales", "audit"), (ORDERS, EVENTS, TOTALS, SECRET)
    )

    stored = read_objects(workspace.engine, SOURCE.id)
    assert stored.schemas == ("audit", "sales")
    assert listed(workspace) == [
        summary(TOTALS),
        summary(SECRET),
        summary(EVENTS),
        summary(ORDERS),
    ]
    assert stored.discovered_at is not None and first is not None
    assert stored.discovered_at >= first
    assert read_object(workspace.engine, SOURCE.id, view) is None
    assert dataset_row(workspace, view) == (True, "sales.order_view", ["id", "region"])
    assert object_id(workspace, "sales", "orders") == orders


AMOUNT = StoredColumn("amount", "numeric(12,2)", "number", allows_nulls=True)
CHANGES = {
    "a column's type": (ID, REGION, AMOUNT),
    "a column's normalised type": (ID, StoredColumn("region", "text", "integer", True), AMOUNT),
    "whether it allows nulls": (ID, StoredColumn("region", "text", "string", False), AMOUNT),
    "a new column": (*ORDERS.columns, StoredColumn("note", "text", "string", True)),
    "a dropped column": ORDERS.columns[:2],
    "the columns' order": (REGION, ID, ORDERS.columns[2]),
    "a renamed column": (ID, StoredColumn("area", "text", "string", True), ORDERS.columns[2]),
}


@pytest.mark.parametrize("change", [*CHANGES, "its kind"])
def test_a_refresh_after_a_column_change_gives_a_new_object_with_the_new_columns_and_keeps_it_chosen(
    workspace: Workspace, change: str
) -> None:
    # CFX-APP-postgres/AC-13, AC-11: the changed object is a new dataset with the columns as they
    # are now, still chosen; the old one stays, replaced, with its columns, for earlier runs.
    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)
    old = object_id(workspace, "sales", "orders")
    save_selection(workspace.engine, SOURCE.id, frozenset({old}))
    changed = ObjectFound(
        **{
            **ORDERS.__dict__,
            **(
                {"kind": "partitioned_table"}
                if change == "its kind"
                else {"columns": CHANGES[change]}
            ),
        }
    )

    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, (changed, *FOUND[1:]))

    new = object_id(workspace, "sales", "orders")
    assert new != old
    opened = read_object(workspace.engine, SOURCE.id, new)
    assert opened is not None
    assert (opened[0].kind, opened[0].chosen) == (changed.kind, True)
    assert opened[1:] == (changed.columns, changed.keys)
    assert read_object(workspace.engine, SOURCE.id, old) is None
    assert dataset_row(workspace, old) == (True, "sales.orders", ["id", "region", "amount"])
    assert chosen(workspace) == {("sales", "orders")}


def test_a_refresh_keeps_an_unchanged_objects_id_and_updates_its_estimate_and_access(
    workspace: Workspace,
) -> None:
    # CFX-APP-postgres/AC-13: same kind and columns, so the same dataset, now with the database's
    # estimate, read access and declared keys; still chosen.
    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)
    orders = object_id(workspace, "sales", "orders")
    secret = object_id(workspace, "audit", "secret")
    save_selection(workspace.engine, SOURCE.id, frozenset({orders}))
    now_orders = ObjectFound(
        **{**ORDERS.__dict__, "estimated_rows": None, "keys": ORDERS.keys[:1]}
    )
    now_secret = ObjectFound(**{**SECRET.__dict__, "estimated_rows": 7, "readable": True})

    save_discovery(
        workspace.engine, SOURCE.id, SCHEMAS, (now_orders, EVENTS, ORDER_VIEW, TOTALS, now_secret)
    )

    assert read_object(workspace.engine, SOURCE.id, orders) == (
        StoredObject(orders, "sales", "orders", "table", None, True, True),
        ORDERS.columns,
        ORDERS.keys[:1],
    )
    assert read_object(workspace.engine, SOURCE.id, secret) == (
        StoredObject(secret, "audit", "secret", "table", 7, True, False),
        SECRET.columns,
        (),
    )
    assert dataset_row(workspace, orders)[0] is False


@pytest.mark.parametrize("columns", [ORDERS.columns, CHANGES["a new column"]])
def test_an_object_that_loses_read_access_is_no_longer_chosen(
    workspace: Workspace, columns: tuple[StoredColumn, ...]
) -> None:
    # CFX-APP-postgres/AC-10: whether its columns changed or not. Getting access back doesn't
    # choose it again: a refresh never adds to the selection.
    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)
    save_selection(
        workspace.engine,
        SOURCE.id,
        frozenset(
            {object_id(workspace, "sales", "orders"), object_id(workspace, "sales", "events")}
        ),
    )
    unreadable = ObjectFound(**{**ORDERS.__dict__, "readable": False, "columns": columns})

    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, (unreadable, *FOUND[1:]))

    assert chosen(workspace) == {("sales", "events")}
    assert ("sales", "orders", "table", 1200, False) in listed(workspace)

    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)

    assert chosen(workspace) == {("sales", "events")}


@pytest.mark.parametrize(
    "values",
    [
        # (schema_name, object_name, object_kind, readable, populated, chosen)
        "('s', 'u', 'materialized_view', 1, 0, 1)",
        "('s', 'u', 'materialized_view', 1, NULL, 0)",
        "(NULL, NULL, NULL, NULL, 1, 0)",
    ],
)
def test_the_database_refuses_a_chosen_object_that_isnt_populated(
    database: sqlite3.Connection, values: str
) -> None:
    # CFX-APP-postgres/AC-10, as agreed for DRIVN-49: a materialized view never refreshed can't be
    # chosen, and only an object says whether it is populated.
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO dataset (id, source_id, name, schema_name, object_name, object_kind,"
            f" readable, populated, chosen) SELECT 'd9', 'p1', 's.u', * FROM (VALUES {values})"
        )


UNPOPULATED = ObjectFound(**{**TOTALS.__dict__, "populated": False})


def test_a_materialized_view_never_refreshed_is_listed_and_cant_be_chosen(
    workspace: Workspace,
) -> None:
    # CFX-APP-postgres/AC-10: listed as not populated, and a selection with it is refused whole.
    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, (*FOUND[:3], UNPOPULATED, SECRET))
    totals = object_id(workspace, "audit", "Odd.Name, é")
    orders = object_id(workspace, "sales", "orders")

    with pytest.raises(NotChoosable):
        save_selection(workspace.engine, SOURCE.id, frozenset({orders, totals}))

    assert chosen(workspace) == set()
    assert read_object(workspace.engine, SOURCE.id, totals)[0] == StoredObject(
        totals, "audit", "Odd.Name, é", "materialized_view", 2, True, False, populated=False
    )


def test_a_refresh_after_refreshing_the_view_lets_it_be_chosen_and_after_emptying_it_unchooses_it(
    workspace: Workspace,
) -> None:
    # CFX-APP-postgres/AC-13: REFRESH MATERIALIZED VIEW, then a refresh in DrivnX; then
    # REFRESH ... WITH NO DATA, and a refresh takes it out of the selection.
    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, (*FOUND[:3], UNPOPULATED, SECRET))
    totals = object_id(workspace, "audit", "Odd.Name, é")

    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, FOUND)
    save_selection(workspace.engine, SOURCE.id, frozenset({totals}))

    assert chosen(workspace) == {("audit", "Odd.Name, é")}

    save_discovery(workspace.engine, SOURCE.id, SCHEMAS, (*FOUND[:3], UNPOPULATED, SECRET))

    assert chosen(workspace) == set()
    assert read_object(workspace.engine, SOURCE.id, totals)[0].populated is False
