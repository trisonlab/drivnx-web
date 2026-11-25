"""`/api/sources`: connecting a CSV or Parquet file by its path, and listing what is connected."""

import os
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from artifacts.profiles import RunInProgress
from artifacts.sources import (
    StoredField,
    StoredParquetSource,
    StoredPostgresSource,
    StoredSource,
    add_csv_source,
    add_parquet_source,
    find_source,
    find_source_by_path,
    list_sources,
    save_corrected_settings,
)
from artifacts.workspace import Workspace
from connectors.csv_files import (
    NOT_REGULAR_FILE,
    CsvError,
    detect_encoding,
    normalised_type,
    read_columns,
    read_page,
    scan_csv,
)
from connectors.parquet_files import (
    NOT_PARQUET,
    ParquetError,
    is_parquet,
    read_metadata,
)
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from local_api.pages import (
    DEFAULT_PAGE_ROWS,
    MAX_OFFSET,
    MAX_PAGE_ROWS,
    PageResponse,
    shown_cell,
)
from local_api.postgres_api import KeptPasswords, PostgresSourceResponse, postgres_response

# A URL such as s3://bucket/events.parquet or file:///data/events.csv. A scheme has two letters
# or more, as Windows reads C://data as a path on drive C.
REMOTE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+://")
RUN_ON_THIS_FILE = (
    "A profile is running on this file. Wait for it to finish, then save your settings."
)
# A database's rows are a chosen object's, at /api/sources/{id}/objects/{object_id}/rows (DRIVN-50).
ONE_OBJECT_AT_A_TIME = "A database is previewed one chosen object at a time."
NO_FILE_SETTINGS = "A database has no file settings."
NO_PARQUET_SETTINGS = "A Parquet file has no file settings."
# Until DRIVN-62 previews a Parquet file.
NO_PARQUET_PREVIEW = "This Parquet file can't be previewed yet."


def _no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain a NUL character")
    return value


class SourceRequest(BaseModel):
    path: Annotated[
        str,
        StringConstraints(min_length=1, max_length=4096),
        AfterValidator(_no_nul),
    ]


class SettingsRequest(BaseModel):
    delimiter: Literal[",", ";", "\t", "|"]
    encoding: Literal["UTF-8", "UTF-16 LE", "UTF-16 BE", "Latin-1", "Windows-1252"]
    has_header: bool
    # None leaves the number to be worked out on every read.
    skip_lines: Annotated[int, Field(ge=0, le=1000)] | None


class ColumnResponse(BaseModel):
    name: str
    physical_type: str
    normalised_type: str


class SourceResponse(BaseModel):
    id: str
    kind: Literal["csv"] = "csv"
    name: str
    path: str
    delimiter: str
    encoding: str
    has_header: bool
    skip_lines: int | None
    size_bytes: int
    modified_at: str
    available: bool
    columns: list[ColumnResponse]


class ParquetSourceResponse(BaseModel):
    id: str
    kind: Literal["parquet"] = "parquet"
    name: str
    path: str
    size_bytes: int
    modified_at: str
    available: bool
    row_count: int
    row_group_count: int
    created_by: str | None
    columns: list[ColumnResponse]


def is_available(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        # is_file() only answers False for a missing file. A folder that has lost its permissions
        # or a dead network drive raises, and must not take the whole source list down with it.
        return False


def _columns(fields: tuple[StoredField, ...]) -> list[ColumnResponse]:
    return [
        ColumnResponse(
            name=field.name,
            physical_type=field.physical_type,
            normalised_type=field.normalised_type,
        )
        for field in fields
    ]


def _parquet_response(source: StoredParquetSource) -> ParquetSourceResponse:
    return ParquetSourceResponse(
        id=source.id,
        name=source.path.name,
        path=str(source.path),
        size_bytes=source.size_bytes,
        modified_at=source.modified_at,
        available=is_available(source.path),
        row_count=source.row_count,
        row_group_count=source.row_group_count,
        created_by=source.created_by,
        columns=_columns(source.fields),
    )


def _file_response(
    source: StoredSource | StoredParquetSource,
) -> SourceResponse | ParquetSourceResponse:
    if isinstance(source, StoredParquetSource):
        return _parquet_response(source)
    return _response(source)


def _response(source: StoredSource) -> SourceResponse:
    return SourceResponse(
        id=source.id,
        name=source.path.name,
        path=str(source.path),
        delimiter=source.delimiter,
        encoding=source.encoding,
        has_header=source.has_header,
        skip_lines=source.skip_lines,
        size_bytes=source.size_bytes,
        modified_at=source.modified_at,
        available=is_available(source.path),
        columns=_columns(source.fields),
    )


def _refuse(message: str) -> HTTPException:
    # The message never repeats the path, so a path can't end up in a log or an error report.
    return HTTPException(status_code=422, detail=message)


def cannot_read(message: str) -> HTTPException:
    # The request was fine, and the file is no longer in a state that can answer it.
    return HTTPException(status_code=409, detail=message)


def modified_at(stat: os.stat_result) -> str:
    return datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()


def current_encoding(source: StoredSource) -> str:
    """Read with the saved encoding unless the file changed, because detecting reads all of it.

    An encoding the user chose always stays.
    """
    if source.corrected:
        return source.encoding
    stat = source.path.stat()
    unchanged = (
        stat.st_size == source.size_bytes and modified_at(stat) == source.modified_at
    )
    return source.encoding if unchanged else detect_encoding(source.path)


def _readable_file(text: str) -> Path:
    if REMOTE.match(text):
        raise _refuse(
            "DrivnX reads files on this computer only. Enter the full path to a file."
        )
    try:
        expanded = Path(text).expanduser()
    except RuntimeError:
        raise _refuse(
            "DrivnX couldn't work out your home directory. Enter the full path."
        ) from None
    if not expanded.is_absolute():
        raise _refuse("Enter the full path to the file, not a relative one.")
    try:
        path = expanded.resolve()
        # A link can lead to a name that isn't valid text, which can't be stored or sent back.
        str(path).encode()
        if path.is_dir():
            raise _refuse(
                "This path is a folder. Enter the path to a CSV, XLSX or Parquet file; "
                "a folder of Parquet files isn't supported yet."
            )
        if not path.exists():
            raise _refuse("There's no file at this path.")
        if not path.is_file():
            raise _refuse(NOT_REGULAR_FILE)
        with path.open("rb"):
            pass
    except UnicodeEncodeError:
        raise _refuse(
            "This path has characters that aren't valid text. "
            "Rename the file or folder and connect it again."
        ) from None
    except PermissionError:
        raise _refuse("DrivnX doesn't have permission to read this file.") from None
    except (OSError, RuntimeError):
        # Python 3.12's resolve() raises RuntimeError on a symlink loop.
        raise _refuse("DrivnX couldn't read this path.") from None
    return path


def sources_router(workspace: Workspace, kept: KeptPasswords) -> APIRouter:
    router = APIRouter(prefix="/api/sources")

    @router.get("")
    def get_sources() -> list[
        SourceResponse | ParquetSourceResponse | PostgresSourceResponse
    ]:
        return [
            postgres_response(source, kept)
            if isinstance(source, StoredPostgresSource)
            else _file_response(source)
            for source in list_sources(workspace.engine)
        ]

    @router.post("", status_code=201)
    def connect_source(
        body: SourceRequest, response: Response
    ) -> SourceResponse | ParquetSourceResponse:
        path = _readable_file(body.path)
        existing = find_source_by_path(workspace.engine, path)
        if existing is not None:
            response.status_code = 200
            return _file_response(existing)
        try:
            parquet = is_parquet(path)
        except OSError:
            # Gone or locked since the checks. The error's text holds the path.
            raise _refuse("DrivnX couldn't read this file.") from None
        if parquet:
            return _connect_parquet(path, response)
        try:
            scan = scan_csv(path, workspace.temp_directory)
            stat = path.stat()
        except CsvError as error:
            # Named as Parquet, and neither Parquet nor text.
            if path.suffix.lower() == ".parquet":
                raise _refuse(NOT_PARQUET) from None
            raise _refuse(str(error)) from None
        except OSError:
            raise _refuse("DrivnX couldn't read this file.") from None
        source = StoredSource(
            id=uuid.uuid4().hex,
            path=path,
            delimiter=scan.detection.delimiter,
            encoding=scan.detection.encoding,
            has_header=scan.detection.has_header,
            size_bytes=stat.st_size,
            modified_at=modified_at(stat),
            fields=tuple(
                StoredField(
                    column.name, column.duckdb_type, normalised_type(column.duckdb_type)
                )
                for column in scan.columns
            ),
        )
        if not add_csv_source(workspace.engine, source):
            # Someone connected the same path while this one was being scanned.
            response.status_code = 200
            existing = find_source_by_path(workspace.engine, path)
            assert existing is not None
            return _file_response(existing)
        return _response(source)

    def _connect_parquet(path: Path, response: Response) -> ParquetSourceResponse:
        try:
            metadata = read_metadata(path, workspace.temp_directory)
            stat = path.stat()
        except ParquetError as error:
            raise _refuse(str(error)) from None
        except OSError:
            raise _refuse("DrivnX couldn't read this file.") from None
        source = StoredParquetSource(
            id=uuid.uuid4().hex,
            path=path,
            size_bytes=stat.st_size,
            modified_at=modified_at(stat),
            row_count=metadata.row_count,
            row_group_count=metadata.row_group_count,
            created_by=metadata.created_by,
            fields=tuple(
                StoredField(
                    column.name, column.duckdb_type, normalised_type(column.duckdb_type)
                )
                for column in metadata.columns
            ),
        )
        if not add_parquet_source(workspace.engine, source):
            # Someone connected the same path while this one was being read.
            response.status_code = 200
            existing = find_source_by_path(workspace.engine, path)
            assert existing is not None
            return _file_response(existing)
        return _parquet_response(source)

    @router.put("/{source_id}/settings")
    def save_settings(source_id: str, body: SettingsRequest) -> SourceResponse:
        source = find_source(workspace.engine, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="There's no such source.")
        if isinstance(source, StoredPostgresSource):
            raise cannot_read(NO_FILE_SETTINGS)
        if isinstance(source, StoredParquetSource):
            raise cannot_read(NO_PARQUET_SETTINGS)
        if not is_available(source.path):
            raise cannot_read(
                "DrivnX can't find this file. It may have moved or been deleted."
            )
        try:
            columns = read_columns(
                source.path,
                body.encoding,
                body.delimiter,
                body.has_header,
                body.skip_lines,
                workspace.temp_directory,
            )
            stat = source.path.stat()
        except CsvError as error:
            raise _refuse(str(error)) from None
        except PermissionError:
            raise cannot_read(
                "DrivnX doesn't have permission to read this file."
            ) from None
        except OSError:
            raise cannot_read("DrivnX couldn't read this file.") from None
        corrected = replace(
            source,
            delimiter=body.delimiter,
            encoding=body.encoding,
            has_header=body.has_header,
            skip_lines=body.skip_lines,
            size_bytes=stat.st_size,
            modified_at=modified_at(stat),
            fields=tuple(
                StoredField(
                    column.name, column.duckdb_type, normalised_type(column.duckdb_type)
                )
                for column in columns
            ),
        )
        try:
            save_corrected_settings(workspace.engine, corrected)
        except RunInProgress:
            raise HTTPException(status_code=409, detail=RUN_ON_THIS_FILE) from None
        return _response(corrected)

    @router.get("/{source_id}/rows")
    def get_rows(
        source_id: str,
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_ROWS)] = DEFAULT_PAGE_ROWS,
    ) -> PageResponse:
        source = find_source(workspace.engine, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="There's no such source.")
        if isinstance(source, StoredPostgresSource):
            raise cannot_read(ONE_OBJECT_AT_A_TIME)
        if isinstance(source, StoredParquetSource):
            raise cannot_read(NO_PARQUET_PREVIEW)
        if not is_available(source.path):
            raise cannot_read(
                "DrivnX can't find this file. It may have moved or been deleted."
            )
        # One row more than the page shows whether there is a next page, without counting rows.
        try:
            page = read_page(
                source.path,
                current_encoding(source),
                source.delimiter,
                source.has_header,
                offset,
                limit + 1,
                workspace.temp_directory,
                source.skip_lines,
            )
        except CsvError as error:
            raise cannot_read(str(error)) from None
        except PermissionError:
            raise cannot_read(
                "DrivnX doesn't have permission to read this file."
            ) from None
        except OSError:
            raise cannot_read("DrivnX couldn't read this file.") from None
        return PageResponse(
            columns=[shown_cell(name) for name in page.columns],
            rows=[[shown_cell(value) for value in row] for row in page.rows[:limit]],
            offset=offset,
            has_next=len(page.rows) > limit,
            skipped_lines=page.skipped_lines,
        )

    return router
