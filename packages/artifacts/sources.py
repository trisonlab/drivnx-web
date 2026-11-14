"""Connected sources, their datasets and fields. Only metadata, never data rows."""

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
    # A database's datasets are the objects the user chooses, which come later (DRIVN-49).
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
