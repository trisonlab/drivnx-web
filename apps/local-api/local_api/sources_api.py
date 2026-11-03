"""`/api/sources`: connecting a CSV by its path, and listing what is connected."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from artifacts.sources import (
    StoredField,
    StoredSource,
    add_csv_source,
    find_source_by_path,
    list_sources,
)
from artifacts.workspace import Workspace
from connectors.csv_files import CsvError, normalised_type, scan_csv
from fastapi import APIRouter, HTTPException, Response
from pydantic import AfterValidator, BaseModel, StringConstraints


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


class ColumnResponse(BaseModel):
    name: str
    physical_type: str
    normalised_type: str


class SourceResponse(BaseModel):
    id: str
    name: str
    path: str
    delimiter: str
    encoding: str
    has_header: bool
    size_bytes: int
    modified_at: str
    available: bool
    columns: list[ColumnResponse]


def _is_available(path: Path) -> bool:
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
        size_bytes=source.size_bytes,
        modified_at=source.modified_at,
        available=_is_available(source.path),
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


def _readable_file(text: str) -> Path:
    try:
        expanded = Path(text).expanduser()
    except RuntimeError:
        raise _refuse(
            "Conflux couldn't work out your home directory. Enter the full path."
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
        raise _refuse("Conflux doesn't have permission to read this file.") from None
    except (OSError, RuntimeError):
        # Python 3.12's resolve() raises RuntimeError on a symlink loop.
        raise _refuse("Conflux couldn't read this path.") from None
    return path


def sources_router(workspace: Workspace) -> APIRouter:
    router = APIRouter(prefix="/api/sources")

    @router.get("")
    def get_sources() -> list[SourceResponse]:
        return [_response(source) for source in list_sources(workspace.engine)]

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
            raise _refuse("Conflux couldn't read this file.") from None
        source = StoredSource(
            id=uuid.uuid4().hex,
            path=path,
            delimiter=scan.detection.delimiter,
            encoding=scan.detection.encoding,
            has_header=scan.detection.has_header,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
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

    return router
