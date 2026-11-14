"""`/api/sources`: connecting a CSV by its path, and listing what is connected."""

import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from artifacts.profiles import RunInProgress
from artifacts.sources import (
    StoredField,
    StoredPostgresSource,
    StoredSource,
    add_csv_source,
    find_source,
    find_source_by_path,
    list_sources,
    save_corrected_settings,
)
from artifacts.workspace import Workspace
from connectors.csv_files import (
    MAX_CELL_CHARACTERS,
    CsvError,
    detect_encoding,
    normalised_type,
    read_columns,
    read_page,
    scan_csv,
)
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from local_api.postgres_api import KeptPasswords, PostgresSourceResponse, postgres_response

DEFAULT_PAGE_ROWS = 50
MAX_PAGE_ROWS = 200
# The largest position DuckDB accepts. Past the end of a file, a page is just empty.
MAX_OFFSET = 2**63 - 1
RUN_ON_THIS_FILE = (
    "A profile is running on this file. Wait for it to finish, then save your settings."
)
# Choosing a database's tables comes later (DRIVN-49), and previewing them with it (DRIVN-50).
NO_PREVIEW_YET = "Previewing a database comes with choosing its tables, which isn't available yet."
NO_FILE_SETTINGS = "A database has no file settings."


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


class CellResponse(BaseModel):
    value: str | None
    cut: bool


class PageResponse(BaseModel):
    columns: list[CellResponse]
    rows: list[list[CellResponse]]
    offset: int
    has_next: bool
    skipped_lines: int


def shown_cell(value: str | None) -> CellResponse:
    if value is None or len(value) <= MAX_CELL_CHARACTERS:
        return CellResponse(value=value, cut=False)
    return CellResponse(value=value[:MAX_CELL_CHARACTERS], cut=True)


def is_available(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        # is_file() only answers False for a missing file. A folder that has lost its permissions
        # or a dead network drive raises, and must not take the whole source list down with it.
        return False


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
        columns=[
            ColumnResponse(
                name=field.name,
                physical_type=field.physical_type,
                normalised_type=field.normalised_type,
            )
            for field in source.fields
        ],
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
            raise _refuse("This path is a folder. Enter the path to a CSV file.")
        if not path.exists():
            raise _refuse("There's no file at this path.")
        if not path.is_file():
            raise _refuse(
                "This path is not a regular file, so it can't be read as a CSV."
            )
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
    def get_sources() -> list[SourceResponse | PostgresSourceResponse]:
        return [
            postgres_response(source, kept.get(source.id))
            if isinstance(source, StoredPostgresSource)
            else _response(source)
            for source in list_sources(workspace.engine)
        ]

    @router.post("", status_code=201)
    def connect_source(body: SourceRequest, response: Response) -> SourceResponse:
        path = _readable_file(body.path)
        existing = find_source_by_path(workspace.engine, path)
        if existing is not None:
            response.status_code = 200
            return _response(existing)
        try:
            scan = scan_csv(path, workspace.temp_directory)
            stat = path.stat()
        except CsvError as error:
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
            return _response(existing)
        return _response(source)

    @router.put("/{source_id}/settings")
    def save_settings(source_id: str, body: SettingsRequest) -> SourceResponse:
        source = find_source(workspace.engine, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="There's no such source.")
        if isinstance(source, StoredPostgresSource):
            raise cannot_read(NO_FILE_SETTINGS)
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
            raise cannot_read(NO_PREVIEW_YET)
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
