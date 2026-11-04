"""Connected sources, their datasets and fields. Only metadata, never data rows."""

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from artifacts.store import write_transaction


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


_SELECT_SOURCES = """
    SELECT id, path, delimiter, encoding, has_header, size_bytes, modified_at, kind
    FROM source
"""


def _with_fields(connection: Connection, row) -> StoredSource:
    fields = connection.execute(
        text(
            """
            SELECT field.name, field.physical_type, field.normalised_type
            FROM field JOIN dataset ON field.dataset_id = dataset.id
            WHERE dataset.source_id = :source_id
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
    )


def list_sources(engine: Engine) -> list[StoredSource]:
    with engine.connect() as connection:
        rows = connection.execute(text(f"{_SELECT_SOURCES} ORDER BY rowid")).all()
        return [_with_fields(connection, row) for row in rows]


def find_source_by_path(engine: Engine, path: Path) -> StoredSource | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(f"{_SELECT_SOURCES} WHERE path = :path"), {"path": str(path)}
        ).first()
        return _with_fields(connection, row) if row else None


def find_source(engine: Engine, source_id: str) -> StoredSource | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(f"{_SELECT_SOURCES} WHERE id = :id"), {"id": source_id}
        ).first()
        return _with_fields(connection, row) if row else None


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
    """The source's dataset. A CSV has exactly one."""
    with engine.connect() as connection:
        dataset = connection.execute(
            text("SELECT id, source_id, name FROM dataset WHERE source_id = :id"),
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
    return True
