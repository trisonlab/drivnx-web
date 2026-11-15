"""Connected sources, their datasets and fields. Only metadata, never data rows."""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from artifacts.profiles import RunInProgress
from artifacts.provenance import now
from artifacts.store import empty_the_wal, write_transaction


@dataclass(frozen=True)
class StoredField:
    name: str
    physical_type: str
    normalised_type: str


@dataclass(frozen=True)
class StoredSource:
    id: str
    path: Path
    delimiter: str
    encoding: str
    has_header: bool
    size_bytes: int
    modified_at: str
    fields: tuple[StoredField, ...]
    kind: str = "csv"
    skip_lines: int | None = None
    corrected: bool = False


@dataclass(frozen=True)
class StoredPostgresSource:
    """A PostgreSQL connection as saved: everything but the password, which is a secret."""

    id: str
    host: str
    port: int
    database: str
    user: str
    tls_mode: str
    ca_path: str | None
    kind: str = "postgres"


_SELECT_SOURCES = """
    SELECT id, path, delimiter, encoding, has_header, size_bytes, modified_at, kind, skip_lines,
           corrected, host, port, database_name, user_name, tls_mode, ca_path
    FROM source
"""


def _postgres(row) -> StoredPostgresSource:
    return StoredPostgresSource(
        id=row.id,
        host=row.host,
        port=row.port,
        database=row.database_name,
        user=row.user_name,
        tls_mode=row.tls_mode,
        ca_path=row.ca_path,
    )


def _stored(connection: Connection, row) -> StoredSource | StoredPostgresSource:
    # A database's datasets are the objects discovery found: `read_objects` lists them.
    return _postgres(row) if row.kind == "postgres" else _with_fields(connection, row)


def _with_fields(connection: Connection, row) -> StoredSource:
    fields = connection.execute(
        text(
            """
            SELECT field.name, field.physical_type, field.normalised_type
            FROM field JOIN dataset ON field.dataset_id = dataset.id
            WHERE dataset.source_id = :source_id AND dataset.replaced_at IS NULL
            ORDER BY field.position
            """
        ),
        {"source_id": row.id},
    )
    return StoredSource(
        id=row.id,
        path=Path(row.path),
        delimiter=row.delimiter,
        encoding=row.encoding,
        has_header=bool(row.has_header),
        size_bytes=row.size_bytes,
        modified_at=row.modified_at,
        fields=tuple(StoredField(*field) for field in fields),
        kind=row.kind,
        skip_lines=row.skip_lines,
        corrected=bool(row.corrected),
    )


def list_sources(engine: Engine) -> list[StoredSource | StoredPostgresSource]:
    with engine.connect() as connection:
        rows = connection.execute(text(f"{_SELECT_SOURCES} ORDER BY rowid")).all()
        return [_stored(connection, row) for row in rows]


def find_source_by_path(engine: Engine, path: Path) -> StoredSource | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(f"{_SELECT_SOURCES} WHERE path = :path"), {"path": str(path)}
        ).first()
        return _with_fields(connection, row) if row else None


def find_source(
    engine: Engine, source_id: str
) -> StoredSource | StoredPostgresSource | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(f"{_SELECT_SOURCES} WHERE id = :id"), {"id": source_id}
        ).first()
        return _stored(connection, row) if row else None


def find_source_by_connection(
    engine: Engine, host: str, port: int, database: str, user: str
) -> StoredPostgresSource | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                f"""
                {_SELECT_SOURCES}
                WHERE host = :host AND port = :port AND database_name = :database
                    AND user_name = :user
                """
            ),
            {"host": host, "port": port, "database": database, "user": user},
        ).first()
        return _postgres(row) if row else None


def add_postgres_source(engine: Engine, source: StoredPostgresSource) -> bool:
    """Save a connection without its password. Returns False if it is already saved."""
    with write_transaction(engine) as connection:
        return bool(
            connection.execute(
                text(
                    """
                    INSERT INTO source
                        (id, kind, host, port, database_name, user_name, tls_mode, ca_path)
                    VALUES
                        (:id, 'postgres', :host, :port, :database, :user, :tls_mode, :ca_path)
                    ON CONFLICT (host, port, database_name, user_name) DO NOTHING
                    """
                ),
                {
                    "id": source.id,
                    "host": source.host,
                    "port": source.port,
                    "database": source.database,
                    "user": source.user,
                    "tls_mode": source.tls_mode,
                    "ca_path": source.ca_path,
                },
            ).rowcount
        )


@dataclass(frozen=True)
class DatasetField:
    id: str
    name: str
    physical_type: str
    normalised_type: str


@dataclass(frozen=True)
class StoredDataset:
    id: str
    source_id: str
    name: str
    fields: tuple[DatasetField, ...]


def read_dataset(engine: Engine, source_id: str) -> StoredDataset:
    """The source's current dataset. A correction replaces it, and earlier ones stay as evidence."""
    with engine.connect() as connection:
        dataset = connection.execute(
            text(
                """
                SELECT id, source_id, name FROM dataset
                WHERE source_id = :id AND replaced_at IS NULL
                """
            ),
            {"id": source_id},
        ).one()
        fields = connection.execute(
            text(
                """
                SELECT id, name, physical_type, normalised_type FROM field
                WHERE dataset_id = :id ORDER BY position
                """
            ),
            {"id": dataset.id},
        )
        return StoredDataset(
            dataset.id,
            dataset.source_id,
            dataset.name,
            tuple(DatasetField(*field) for field in fields),
        )


def read_field_names(engine: Engine, dataset_id: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, name FROM field WHERE dataset_id = :id"), {"id": dataset_id}
        ).all()
    return {row.id: row.name for row in rows}


def _insert_dataset(connection: Connection, source: StoredSource) -> None:
    dataset_id = uuid.uuid4().hex
    connection.execute(
        text(
            "INSERT INTO dataset (id, source_id, name) VALUES (:id, :source_id, :name)"
        ),
        {"id": dataset_id, "source_id": source.id, "name": source.path.name},
    )
    connection.execute(
        text(
            """
            INSERT INTO field
                (id, dataset_id, position, name, physical_type, normalised_type)
            VALUES
                (:id, :dataset_id, :position, :name, :physical_type, :normalised_type)
            """
        ),
        [
            {
                "id": uuid.uuid4().hex,
                "dataset_id": dataset_id,
                "position": position,
                "name": field.name,
                "physical_type": field.physical_type,
                "normalised_type": field.normalised_type,
            }
            for position, field in enumerate(source.fields)
        ],
    )


def add_csv_source(engine: Engine, source: StoredSource) -> bool:
    """Save a source with its dataset and fields. Returns False if the path is already connected."""
    with write_transaction(engine) as connection:
        added = connection.execute(
            text(
                """
                INSERT INTO source
                    (id, kind, path, delimiter, encoding, has_header, size_bytes, modified_at)
                VALUES
                    (:id, 'csv', :path, :delimiter, :encoding, :has_header, :size_bytes, :modified_at)
                ON CONFLICT (path) DO NOTHING
                """
            ),
            {
                "id": source.id,
                "path": str(source.path),
                "delimiter": source.delimiter,
                "encoding": source.encoding,
                "has_header": int(source.has_header),
                "size_bytes": source.size_bytes,
                "modified_at": source.modified_at,
            },
        ).rowcount
        if not added:
            return False
        _insert_dataset(connection, source)
    return True


@dataclass(frozen=True)
class StoredColumn:
    name: str
    physical_type: str
    normalised_type: str
    allows_nulls: bool


@dataclass(frozen=True)
class StoredKey:
    """A declared key, by column names in key order. A foreign key's table may not be listed."""

    kind: str
    name: str
    columns: tuple[str, ...]
    referenced_schema: str | None = None
    referenced_object: str | None = None
    referenced_columns: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ObjectFound:
    """A table, partitioned table, view or materialized view, as discovery found it."""

    schema: str
    name: str
    kind: str
    estimated_rows: int | None
    readable: bool
    columns: tuple[StoredColumn, ...]
    keys: tuple[StoredKey, ...]
    # False only for a materialized view never refreshed, which no one can read.
    populated: bool = True


@dataclass(frozen=True)
class StoredObject:
    id: str
    schema: str
    name: str
    kind: str
    estimated_rows: int | None
    readable: bool
    chosen: bool
    populated: bool = True


@dataclass(frozen=True)
class StoredObjects:
    """What the last discovery found, objects ordered by schema then name."""

    discovered_at: str | None
    schemas: tuple[str, ...]
    objects: tuple[StoredObject, ...]


def _current_columns(
    connection: Connection, source_id: str
) -> dict[str, tuple[StoredColumn, ...]]:
    """Each current object's columns, by dataset id, in one query."""
    rows = connection.execute(
        text(
            """
            SELECT field.dataset_id, field.name, field.physical_type, field.normalised_type,
                   field.allows_nulls
            FROM field JOIN dataset ON field.dataset_id = dataset.id
            WHERE dataset.source_id = :source_id AND dataset.replaced_at IS NULL
                AND dataset.object_name IS NOT NULL
            ORDER BY field.dataset_id, field.position
            """
        ),
        {"source_id": source_id},
    )
    columns: dict[str, list[StoredColumn]] = {}
    for row in rows:
        columns.setdefault(row.dataset_id, []).append(
            StoredColumn(row.name, row.physical_type, row.normalised_type, bool(row.allows_nulls))
        )
    return {dataset_id: tuple(found) for dataset_id, found in columns.items()}


def _insert_object(
    connection: Connection, source_id: str, found: ObjectFound, chosen: bool
) -> None:
    dataset_id = uuid.uuid4().hex
    connection.execute(
        text(
            """
            INSERT INTO dataset
                (id, source_id, name, schema_name, object_name, object_kind, estimated_rows,
                 readable, populated, chosen)
            VALUES
                (:id, :source_id, :name, :schema, :object, :kind, :estimated_rows, :readable,
                 :populated, :chosen)
            """
        ),
        {
            "id": dataset_id,
            "source_id": source_id,
            "name": f"{found.schema}.{found.name}",
            "schema": found.schema,
            "object": found.name,
            "kind": found.kind,
            "estimated_rows": found.estimated_rows,
            "readable": int(found.readable),
            "populated": int(found.populated),
            "chosen": int(chosen),
        },
    )
    if found.columns:
        connection.execute(
            text(
                """
                INSERT INTO field
                    (id, dataset_id, position, name, physical_type, normalised_type,
                     allows_nulls)
                VALUES
                    (:id, :dataset_id, :position, :name, :physical_type, :normalised_type,
                     :allows_nulls)
                """
            ),
            [
                {
                    "id": uuid.uuid4().hex,
                    "dataset_id": dataset_id,
                    "position": position,
                    "name": column.name,
                    "physical_type": column.physical_type,
                    "normalised_type": column.normalised_type,
                    "allows_nulls": int(column.allows_nulls),
                }
                for position, column in enumerate(found.columns)
            ],
        )
    _insert_keys(connection, dataset_id, found.keys)


def _insert_keys(connection: Connection, dataset_id: str, keys: tuple[StoredKey, ...]) -> None:
    if not keys:
        return
    connection.execute(
        text(
            """
            INSERT INTO declared_key
                (id, dataset_id, kind, name, columns, referenced_schema, referenced_object,
                 referenced_columns)
            VALUES
                (:id, :dataset_id, :kind, :name, :columns, :referenced_schema,
                 :referenced_object, :referenced_columns)
            """
        ),
        [
            {
                "id": uuid.uuid4().hex,
                "dataset_id": dataset_id,
                "kind": key.kind,
                "name": key.name,
                # Names, not field ids: a referenced table may not be listed.
                "columns": json.dumps(key.columns),
                "referenced_schema": key.referenced_schema,
                "referenced_object": key.referenced_object,
                "referenced_columns": None
                if key.referenced_columns is None
                else json.dumps(key.referenced_columns),
            }
            for key in keys
        ],
    )


_SELECT_OBJECTS = """
    SELECT id, schema_name, object_name, object_kind, estimated_rows, readable, chosen, populated
    FROM dataset
    WHERE source_id = :source_id AND replaced_at IS NULL AND object_name IS NOT NULL
"""


def _object(row) -> StoredObject:
    return StoredObject(
        id=row.id,
        schema=row.schema_name,
        name=row.object_name,
        kind=row.object_kind,
        estimated_rows=row.estimated_rows,
        readable=bool(row.readable),
        chosen=bool(row.chosen),
        populated=bool(row.populated),
    )


def save_discovery(
    engine: Engine, source_id: str, schemas: tuple[str, ...], objects: tuple[ObjectFound, ...]
) -> None:
    """Save what a discovery found as the source's current objects.

    An object that is gone gets `replaced_at` and stays, so earlier runs still point at what they
    read (ADR 0002). One transaction, so a refresh never half-applies.
    """
    at = now()
    with write_transaction(engine) as connection:
        current = {
            (row.schema_name, row.object_name): row
            for row in connection.execute(text(_SELECT_OBJECTS), {"source_id": source_id})
        }
        columns = _current_columns(connection, source_id)
        found_names = {(found.schema, found.name) for found in objects}
        for name, row in current.items():
            if name not in found_names:
                _replace(connection, row.id, at)
        for found in objects:
            row = current.get((found.schema, found.name))
            if row is None:
                _insert_object(connection, source_id, found, chosen=False)
            elif (row.object_kind, columns.get(row.id, ())) != (found.kind, found.columns):
                # As the CSV correction rule: what changed is a new dataset (ADR 0002).
                _replace(connection, row.id, at)
                _insert_object(
                    connection,
                    source_id,
                    found,
                    chosen=bool(row.chosen) and found.readable and found.populated,
                )
            else:
                _update_object(connection, row.id, found)
        connection.execute(
            text("DELETE FROM database_schema WHERE source_id = :id"), {"id": source_id}
        )
        if schemas:
            connection.execute(
                text("INSERT INTO database_schema (source_id, name) VALUES (:source_id, :name)"),
                [{"source_id": source_id, "name": name} for name in schemas],
            )
        connection.execute(
            text("UPDATE source SET discovered_at = :now WHERE id = :id"),
            {"id": source_id, "now": at},
        )


def _replace(connection: Connection, dataset_id: str, at: str) -> None:
    connection.execute(
        text("UPDATE dataset SET replaced_at = :at WHERE id = :id"),
        {"id": dataset_id, "at": at},
    )


def _update_object(connection: Connection, dataset_id: str, found: ObjectFound) -> None:
    """The same dataset, with the database's estimate, read access, whether it's populated and
    its keys as they are now."""
    # An object that can no longer be read is no longer chosen (AC-10).
    connection.execute(
        text(
            """
            UPDATE dataset
            SET estimated_rows = :estimated_rows, readable = :readable, populated = :populated,
                chosen = chosen AND :readable AND :populated
            WHERE id = :id
            """
        ),
        {
            "id": dataset_id,
            "estimated_rows": found.estimated_rows,
            "readable": int(found.readable),
            "populated": int(found.populated),
        },
    )
    connection.execute(
        text("DELETE FROM declared_key WHERE dataset_id = :id"), {"id": dataset_id}
    )
    _insert_keys(connection, dataset_id, found.keys)


def read_objects(engine: Engine, source_id: str) -> StoredObjects:
    """The objects the last discovery found. Saved, so it needs no password or server."""
    with engine.connect() as connection:
        discovered_at = connection.execute(
            text("SELECT discovered_at FROM source WHERE id = :id"), {"id": source_id}
        ).scalar_one()
        schemas = connection.execute(
            text("SELECT name FROM database_schema WHERE source_id = :id ORDER BY name"),
            {"id": source_id},
        ).scalars()
        objects = connection.execute(
            text(f"{_SELECT_OBJECTS} ORDER BY schema_name, object_name"),
            {"source_id": source_id},
        )
        return StoredObjects(
            discovered_at, tuple(schemas), tuple(_object(row) for row in objects)
        )


class NotChoosable(Exception):
    """An id isn't a current object of this source that can be read: the user has read access
    and, for a materialized view, it has been refreshed."""


def save_selection(engine: Engine, source_id: str, object_ids: frozenset[str]) -> None:
    """Choose exactly these objects of the source, the others not.

    Raise `NotChoosable`, and change nothing, if any id isn't a current object of this source that
    the user can read. Checked under the write lock, so a refresh can't change them in between.
    """
    with write_transaction(engine) as connection:
        current = connection.execute(text(_SELECT_OBJECTS), {"source_id": source_id}).all()
        if not object_ids <= {row.id for row in current if row.readable and row.populated}:
            raise NotChoosable
        if not current:
            return
        connection.execute(
            text("UPDATE dataset SET chosen = :chosen WHERE id = :id"),
            [{"id": row.id, "chosen": int(row.id in object_ids)} for row in current],
        )


def read_object(
    engine: Engine, source_id: str, object_id: str
) -> tuple[StoredObject, tuple[StoredColumn, ...], tuple[StoredKey, ...]] | None:
    """A current object of this source with its columns and declared keys, or None."""
    with engine.connect() as connection:
        row = connection.execute(
            text(f"{_SELECT_OBJECTS} AND id = :id"), {"source_id": source_id, "id": object_id}
        ).first()
        if row is None:
            return None
        columns = connection.execute(
            text(
                """
                SELECT name, physical_type, normalised_type, allows_nulls FROM field
                WHERE dataset_id = :id ORDER BY position
                """
            ),
            {"id": object_id},
        )
        keys = connection.execute(
            text(
                """
                SELECT kind, name, columns, referenced_schema, referenced_object,
                       referenced_columns
                FROM declared_key WHERE dataset_id = :id ORDER BY rowid
                """
            ),
            {"id": object_id},
        )
        return (
            _object(row),
            tuple(
                StoredColumn(name, physical_type, normalised_type, bool(allows_nulls))
                for name, physical_type, normalised_type, allows_nulls in columns
            ),
            tuple(
                StoredKey(
                    key.kind,
                    key.name,
                    tuple(json.loads(key.columns)),
                    key.referenced_schema,
                    key.referenced_object,
                    None
                    if key.referenced_columns is None
                    else tuple(json.loads(key.referenced_columns)),
                )
                for key in keys
            ),
        )


def _names(source: StoredSource) -> list[str]:
    return [field.name for field in source.fields]


def save_corrected_settings(engine: Engine, source: StoredSource) -> None:
    """Save a user's settings, or raise `RunInProgress` while a run reads this source.

    New settings or columns get a new current dataset. Earlier datasets stay, so what a run
    measured still points at what it read (ADR 0002).
    """
    with write_transaction(engine) as connection:
        # Under the write lock, so a run can't start between this check and the save.
        running = connection.execute(
            text(
                """
                SELECT 1 FROM profile_run
                JOIN dataset ON dataset.id = json_extract(profile_run.scope, '$.dataset_id')
                WHERE profile_run.status = 'running' AND dataset.source_id = :id
                LIMIT 1
                """
            ),
            {"id": source.id},
        ).first()
        if running:
            raise RunInProgress
        row = connection.execute(
            text(f"{_SELECT_SOURCES} WHERE id = :id"), {"id": source.id}
        ).one()
        current = _with_fields(connection, row)
        same_reading = (
            current.delimiter == source.delimiter
            and current.encoding == source.encoding
            and current.has_header == source.has_header
            and current.skip_lines == source.skip_lines
            and current.fields == source.fields
        )
        connection.execute(
            text(
                """
                UPDATE source
                SET delimiter = :delimiter, encoding = :encoding, has_header = :has_header,
                    skip_lines = :skip_lines, size_bytes = :size_bytes,
                    modified_at = :modified_at, corrected = 1
                WHERE id = :id
                """
            ),
            {
                "id": source.id,
                "delimiter": source.delimiter,
                "encoding": source.encoding,
                "has_header": int(source.has_header),
                "skip_lines": source.skip_lines,
                "size_bytes": source.size_bytes,
                "modified_at": source.modified_at,
            },
        )
        if same_reading:
            return
        connection.execute(
            text(
                """
                UPDATE dataset SET replaced_at = :now
                WHERE source_id = :id AND replaced_at IS NULL
                """
            ),
            {"id": source.id, "now": now()},
        )
        if _names(current) != _names(source):
            # The row may hold personal data (ADR 0004). Field ids stay, so evidence still points here.
            connection.execute(
                text(
                    """
                    UPDATE field SET name = 'column' || position
                    WHERE dataset_id IN (
                        SELECT id FROM dataset
                        WHERE source_id = :id AND replaced_at IS NOT NULL
                    )
                    """
                ),
                {"id": source.id},
            )
        _insert_dataset(connection, source)
    if _names(current) != _names(source):
        empty_the_wal(engine)
