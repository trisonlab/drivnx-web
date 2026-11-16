"""A page of a preview, for a file and for a database's object alike (first slice AC-28 and AC-29)."""

from connectors.csv_files import MAX_CELL_CHARACTERS
from pydantic import BaseModel

DEFAULT_PAGE_ROWS = 50
MAX_PAGE_ROWS = 200
# The largest position DuckDB accepts. Past the end of a file, a page is just empty.
MAX_OFFSET = 2**63 - 1


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
