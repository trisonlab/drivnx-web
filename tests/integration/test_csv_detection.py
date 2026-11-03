"""Detecting how a CSV is written, CFX-APP-first-slice/AC-22."""

import gzip
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from connectors.csv_files import (
    CsvColumn,
    CsvDetection,
    CsvError,
    detect_encoding,
    duckdb_connection,
    normalised_type,
    read_page,
    scan_csv,
)

CSV_DIR = Path(__file__).parents[2] / "testdata" / "csv"


@pytest.mark.parametrize(
    ("file", "delimiter", "encoding", "has_header"),
    [
        ("comma_utf8.csv", ",", "UTF-8", True),
        ("semicolon_utf8.csv", ";", "UTF-8", True),
        ("tab_utf8.csv", "\t", "UTF-8", True),
        ("pipe_utf8.csv", "|", "UTF-8", True),
        ("comma_utf8_bom.csv", ",", "UTF-8", True),
        ("comma_utf16_le.csv", ",", "UTF-16 LE", True),
        ("comma_utf16_be.csv", ",", "UTF-16 BE", True),
        ("comma_latin1.csv", ",", "Latin-1", True),
        ("prices_windows1252.csv", ",", "Windows-1252", True),
        ("no_header_typed.csv", ",", "UTF-8", False),
        ("header_all_text.csv", ",", "UTF-8", True),
        ("crlf_utf8.csv", ",", "UTF-8", True),
        ("quoted_delimiter.csv", ",", "UTF-8", True),
        ("one_column.csv", ",", "UTF-8", True),
        ("header_only.csv", ",", "UTF-8", True),
    ],
)
def test_detection_matches_how_the_file_was_written(
    tmp_path: Path, file: str, delimiter: str, encoding: str, has_header: bool
) -> None:
    # CFX-APP-first-slice/AC-22
    scan = scan_csv(CSV_DIR / file, tmp_path)

    assert scan.detection == CsvDetection(delimiter, encoding, has_header)


@pytest.mark.parametrize(
    ("file", "delimiter", "columns"),
    [
        ("tab_commas_in_text.csv", "\t", ["id", "address"]),
        ("semicolon_commas_in_text.csv", ";", ["id", "address"]),
        ("pipe_commas_in_text.csv", "|", ["id", "address"]),
        ("extra_field_in_a_row.csv", ",", ["id", "name", "amount"]),
        ("short_row.csv", ",", ["id", "name", "amount"]),
        ("title_line.csv", ",", ["id", "name", "amount"]),
    ],
)
def test_realistic_files_keep_their_delimiter_and_header(
    tmp_path: Path, file: str, delimiter: str, columns: list[str]
) -> None:
    # CFX-APP-first-slice/AC-22 and CFX-APP-first-slice/AC-26: a wrong delimiter skips the real
    # header and makes the first data row the column names, which then get saved.
    scan = scan_csv(CSV_DIR / file, tmp_path)

    assert scan.detection == CsvDetection(delimiter, "UTF-8", True)
    assert [column.name for column in scan.columns] == columns


def test_an_all_text_file_without_a_header_uses_its_first_row_as_column_names(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-22: this can't be told apart from a header, and the screen says so.
    scan = scan_csv(CSV_DIR / "no_header_all_text.csv", tmp_path)

    assert scan.detection.has_header is True
    assert [column.name for column in scan.columns] == ["Ann", "Paris"]


@pytest.mark.parametrize(
    ("file", "delimiter", "columns"),
    [
        ("no_header_commas_in_text.csv", ",", ["1\t1 Main St", "Paris", "France"]),
        ("one_column_commas_in_text.csv", ",", ["1 Main St", "Paris", "France"]),
        ("title_line_tab_commas_in_text.csv", ",", ["1\t1 Main St", "Paris", "France"]),
        ("header_one_field_short.csv", ",", ["Ann", "Paris", "France"]),
        ("no_header_first_row_fits_no_type.csv", "\t", ["Ann", "N/A", "2024-01-15"]),
    ],
)
def test_a_row_may_become_the_column_names_when_a_header_cannot_be_told_from_data(
    tmp_path: Path, file: str, delimiter: str, columns: list[str]
) -> None:
    # CFX-APP-first-slice/AC-22: the accepted limit. If one of these starts failing, detection has
    # improved, so AC-22, AC-26 and threat R3 can be narrowed.
    scan = scan_csv(CSV_DIR / file, tmp_path)

    assert scan.detection.delimiter == delimiter
    assert scan.detection.has_header is True
    assert [column.name for column in scan.columns] == columns


def test_a_file_without_a_header_gets_generated_column_names(tmp_path: Path) -> None:
    # CFX-APP-first-slice/AC-22
    scan = scan_csv(CSV_DIR / "no_header_typed.csv", tmp_path)

    assert [column.name for column in scan.columns] == [f"column{i}" for i in range(5)]


@pytest.mark.parametrize(
    "file",
    [
        "comma_utf8.csv",
        "comma_utf8_bom.csv",
        "comma_utf16_le.csv",
        "comma_utf16_be.csv",
        "comma_latin1.csv",
    ],
)
def test_every_encoding_gives_the_same_columns(tmp_path: Path, file: str) -> None:
    # CFX-APP-first-slice/AC-24: a BOM or a wrong decode would corrupt the first column's name.
    scan = scan_csv(CSV_DIR / file, tmp_path)

    assert scan.columns == (
        CsvColumn("id", "BIGINT"),
        CsvColumn("name", "VARCHAR"),
        CsvColumn("city", "VARCHAR"),
        CsvColumn("amount", "DOUBLE"),
        CsvColumn("joined", "DATE"),
    )


@pytest.mark.parametrize(
    "name",
    [
        "with space.csv",
        "it's quoted.csv",
        'double"quote.csv',
        "report[1].csv",
        "star*name.csv",
        "what?.csv",
        "café données.csv",
        "semi;colon.csv",
        "%20encoded.csv",
        "s3:name.csv",
        "percent%.csv",
        "plain.csv.gz",
        "plain.csv.zst",
    ],
)
@pytest.mark.parametrize("source", ["comma_utf8.csv", "prices_windows1252.csv"])
def test_awkward_file_names_open_the_file_they_name(
    tmp_path: Path, name: str, source: str
) -> None:
    # A name is not a pattern: read_csv treats * ? [ as wildcards.
    work = tmp_path / "files"
    work.mkdir()
    (work / name).write_bytes((CSV_DIR / source).read_bytes())
    (work / "report1.csv").write_bytes(b"wrong,file\n1,2\n")
    (work / "starXname.csv").write_bytes(b"wrong,file\n1,2\n")
    (work / "whatX.csv").write_bytes(b"wrong,file\n1,2\n")

    scan = scan_csv(work / name, tmp_path)

    assert scan.columns[0].name in ("id", "item")


@pytest.mark.parametrize("directory", ["dir[1]", "dir*", "what?"])
def test_awkward_directory_names_open_the_file_they_name(
    tmp_path: Path, directory: str
) -> None:
    work = tmp_path / directory
    work.mkdir()
    (work / "data.csv").write_bytes((CSV_DIR / "comma_utf8.csv").read_bytes())
    decoy = tmp_path / "dir1"
    decoy.mkdir()
    (decoy / "data.csv").write_bytes(b"wrong,file\n1,2\n")

    assert scan_csv(work / "data.csv", tmp_path).columns[0].name == "id"


@pytest.mark.parametrize(
    ("file", "message"),
    [
        ("empty.csv", "empty"),
        ("utf16_no_bom.csv", "UTF-16"),
        ("binary.csv", "UTF-16"),
    ],
)
def test_files_that_cannot_be_read_get_a_specific_error(
    tmp_path: Path, file: str, message: str
) -> None:
    # CFX-APP-first-slice/AC-21
    with pytest.raises(CsvError, match=message):
        scan_csv(CSV_DIR / file, tmp_path)


def test_a_real_gzip_file_is_refused(tmp_path: Path) -> None:
    # CFX-APP-first-slice/AC-21: compressed files aren't in scope, whatever they are named.
    compressed = tmp_path / "data.csv.gz"
    compressed.write_bytes(gzip.compress((CSV_DIR / "comma_utf8.csv").read_bytes(), mtime=0))

    with pytest.raises(CsvError, match="no readable text encoding"):
        scan_csv(compressed, tmp_path)


@pytest.mark.parametrize("bad_byte_at", ["the start", "the end"])
def test_a_utf8_bom_on_a_file_that_is_not_utf8_is_refused(
    tmp_path: Path, bad_byte_at: str
) -> None:
    # CFX-APP-first-slice/AC-21: read as Latin-1, the BOM would corrupt the first column's name.
    rows = b"1,Paris\n" * 3
    bad = b"2,Orl\xe9ans\n"
    mixed = tmp_path / "mixed.csv"
    mixed.write_bytes(
        b"\xef\xbb\xbfid,city\n" + (bad + rows if bad_byte_at == "the start" else rows + bad)
    )

    with pytest.raises(CsvError, match="marked as UTF-8"):
        scan_csv(mixed, tmp_path)


def test_a_bad_byte_late_in_the_file_changes_the_encoding_detected(
    tmp_path: Path,
) -> None:
    # A byte past the sniffer's sample would otherwise fail much later, on a later page.
    late = tmp_path / "late.csv"
    late.write_bytes(b"id,city\n" + b"1,Paris\n" * 400_000 + b"2,Orl\xe9ans\n")

    assert scan_csv(late, tmp_path).detection.encoding == "Latin-1"


def test_a_multibyte_character_split_across_read_chunks_is_still_utf8(
    tmp_path: Path,
) -> None:
    padded = tmp_path / "padded.csv"
    first_line = b"id,city\n"
    padding = b"x" * (1024 * 1024 - len(first_line) - 3)
    padded.write_bytes(first_line + b"1," + padding + "é".encode() + b"\n")

    assert scan_csv(padded, tmp_path).detection.encoding == "UTF-8"


@pytest.mark.parametrize("encoding_name", ["cp1252", "utf-16-be"])
def test_a_large_file_that_must_be_decoded_in_memory_is_refused(
    tmp_path: Path, encoding_name: str
) -> None:
    # CFX-APP-first-slice/AC-21
    big = tmp_path / "big.csv"
    line = "id,price\n" + "1,€5\n" * (21 * 1024 * 1024 // 5 + 1)
    prefix = b"\xfe\xff" if encoding_name == "utf-16-be" else b""
    big.write_bytes(
        prefix + line.encode(encoding_name) * (5 if encoding_name == "cp1252" else 3)
    )

    with pytest.raises(CsvError, match="Save it as UTF-8"):
        scan_csv(big, tmp_path)


@pytest.mark.parametrize("file", ["emoji_utf16_le.csv", "emoji_utf16_be.csv"])
def test_utf16_with_characters_above_ffff_connects(tmp_path: Path, file: str) -> None:
    # CFX-APP-first-slice/AC-20 and CFX-APP-first-slice/AC-22: DuckDB refuses these in little-endian.
    scan = scan_csv(CSV_DIR / file, tmp_path)

    assert scan.detection.delimiter == ","
    assert [column.name for column in scan.columns] == ["id", "note"]


def large_utf16_le(path: Path, last_row: bytes) -> None:
    """Just over 100 MB, so the last row is far past anything the sniffer samples."""
    row = "1,Paris\n".encode("utf-16-le")
    with path.open("wb") as file:
        file.write(b"\xff\xfe" + "id,city\n".encode("utf-16-le"))
        for _ in range(101):
            file.write(row * (1024 * 1024 // len(row)))
        file.write(last_row)


def test_a_character_above_ffff_late_in_a_large_utf16_le_file_gets_the_over_limit_error(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-21: it would otherwise connect, then fail when the rows are read.
    big = tmp_path / "big.csv"
    large_utf16_le(big, "2,😀\n".encode("utf-16-le"))

    with pytest.raises(CsvError, match="Save it as UTF-8"):
        scan_csv(big, tmp_path)


def test_a_large_utf16_le_file_without_those_characters_still_connects(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-20: only the files DuckDB can't read have the 100 MB limit.
    big = tmp_path / "big.csv"
    large_utf16_le(big, "2,Orléans €\n".encode("utf-16-le"))

    assert scan_csv(big, tmp_path).detection == CsvDetection(",", "UTF-16 LE", True)


@pytest.mark.parametrize(
    "bad_end",
    [
        pytest.param(b"2", id="cut short"),
        pytest.param("2,\ud83d,x\n".encode("utf-16-le", "surrogatepass"), id="lone surrogate"),
    ],
)
def test_broken_utf16_le_is_refused_even_late_in_the_file(
    tmp_path: Path, bad_end: bytes
) -> None:
    # CFX-APP-first-slice/AC-21
    broken = tmp_path / "broken.csv"
    large_utf16_le(broken, bad_end)

    with pytest.raises(CsvError, match="can't be read as a CSV"):
        scan_csv(broken, tmp_path)


def test_a_line_too_long_for_the_csv_reader_gets_a_specific_error(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-21
    long_line = tmp_path / "long.csv"
    long_line.write_bytes(b"a,b\n1," + b"x" * (3 * 1024 * 1024) + b"\n")

    with pytest.raises(CsvError, match="can't be read as a CSV"):
        scan_csv(long_line, tmp_path)


def test_duckdb_spills_where_it_is_told_and_cannot_fetch_extensions(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-49, and nothing may leave the machine or arrive from outside it.
    spill = tmp_path / "duckdb-tmp"
    spill.mkdir()

    with duckdb_connection(spill) as connection:
        settings = dict(
            connection.execute(
                "SELECT name, value FROM duckdb_settings() WHERE name IN "
                "('temp_directory', 'autoinstall_known_extensions', 'autoload_known_extensions')"
            ).fetchall()
        )

    assert settings == {
        "temp_directory": str(spill),
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
    }


def test_a_sort_too_big_for_memory_spills_inside_the_given_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-first-slice/AC-49: spill files would hold source data.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    spill = tmp_path / "duckdb-tmp"
    spill.mkdir()

    with duckdb_connection(spill) as connection:
        connection.execute("SET memory_limit = '50MB'; SET threads = 1")
        connection.execute("SET preserve_insertion_order = false")
        connection.execute(
            "SELECT i, md5(i::VARCHAR) AS m FROM range(2000000) t(i) ORDER BY m"
        ).fetchone()
        used = list(spill.iterdir())

    assert used, "DuckDB didn't use the directory, so this test proves nothing"
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize(
    ("duckdb_type", "expected"),
    [
        ("BIGINT", "integer"),
        ("DOUBLE", "number"),
        ("DECIMAL(18,3)", "number"),
        ("BOOLEAN", "boolean"),
        ("DATE", "date"),
        ("TIME", "time"),
        ("TIMESTAMP", "timestamp"),
        ("TIMESTAMP WITH TIME ZONE", "timestamp"),
        ("VARCHAR", "string"),
        ("SOMETHING_NEW", "string"),
    ],
)
def test_duckdb_types_map_to_the_contract_vocabulary(
    duckdb_type: str, expected: str
) -> None:
    # ADR 0003: these are the logical types contract rules can use.
    assert normalised_type(duckdb_type) == expected


def test_duckdb_writes_nothing_to_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-first-slice/AC-49
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    scan_csv(CSV_DIR / "comma_utf8.csv", tmp_path)

    assert list(cwd.iterdir()) == []


def test_a_hash_in_the_header_and_first_row_stays_in_the_file_it_describes(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-22: DuckDB can decide # marks comments and lose the header to it.
    path = tmp_path / "hash.csv"
    path.write_text("#id,name\n#1,a\n2,b\n3,c\n4,d\n")

    scan = scan_csv(path, tmp_path)

    assert [column.name for column in scan.columns] == ["#id", "name"]


def finishes(call: Callable[[], object]) -> bool:
    """A FIFO blocks whoever opens it, so a regression must fail here and not hang the run."""
    errors: list[CsvError] = []

    def run() -> None:
        try:
            call()
        except CsvError as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not errors or "regular file" in str(errors[0])
    return not thread.is_alive()


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(lambda path, temp: detect_encoding(path), id="detecting"),
        pytest.param(lambda path, temp: scan_csv(path, temp), id="scanning"),
        pytest.param(
            lambda path, temp: read_page(path, "UTF-8", ",", True, 0, 1, temp),
            id="reading a page",
        ),
        pytest.param(
            lambda path, temp: read_page(path, "UTF-16 LE", ",", True, 0, 1, temp),
            id="reading UTF-16",
        ),
        pytest.param(
            lambda path, temp: read_page(path, "Windows-1252", ",", True, 0, 1, temp),
            id="decoding in memory",
        ),
    ],
)
def test_a_file_that_becomes_a_fifo_is_refused_rather_than_waited_on(
    tmp_path: Path, read: Callable[[Path, Path], object]
) -> None:
    # CFX-APP-first-slice/AC-31: found reviewing T06, a file can change just after it was checked.
    fifo = tmp_path / "fifo.csv"
    os.mkfifo(fifo)

    assert finishes(lambda: read(fifo, tmp_path))


SLOW_QUERY = """
import sys, time
from pathlib import Path
from connectors.csv_files import duckdb_connection

with duckdb_connection(Path(sys.argv[1])) as connection:
    connection.execute("SET threads = 1")
    started = time.monotonic()
    connection.execute("SELECT sum(hash(i)) FROM range(1000000000) t(i)").fetchone()
    print(f"elapsed {time.monotonic() - started}", file=sys.stderr)
"""


def test_a_slow_query_draws_no_progress_bar(tmp_path: Path) -> None:
    # Found reviewing T06: a deep page drew one on the user's terminal. It's on by default in a normal
    # process but off under pytest, and it only appears after two seconds, hence this slow subprocess.
    result = subprocess.run(
        [sys.executable, "-c", SLOW_QUERY, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    elapsed = float(result.stderr.split("elapsed ")[1])
    assert elapsed > 2, "too fast to have drawn a bar, so this test proves nothing"
    assert "%" not in result.stdout


def test_long_cells_are_cut_before_they_reach_python(tmp_path: Path) -> None:
    # Found reviewing T06: 201 rows of 1.9 MB cells peaked at 1.8 GB. One character over the limit
    # is kept, so the API can tell a cell that was cut from one that fits exactly.
    path = tmp_path / "long.csv"
    path.write_text(f"id,note\n1,{'x' * 5000}\n2,{'y' * 1000}\n")

    page = read_page(path, "UTF-8", ",", True, 0, 2, tmp_path)

    assert [len(row[1]) for row in page.rows] == [1001, 1000]


def test_values_starting_with_a_hash_count_when_detecting_a_columns_type(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-24: read as comments, they would leave this column looking like numbers.
    path = tmp_path / "hash.csv"
    path.write_text("issue,title\n#101,a\n#102,b\n103,c\n104,d\n105,e\n")

    scan = scan_csv(path, tmp_path)

    assert scan.columns[0] == CsvColumn("issue", "VARCHAR")


PAGING_A_DECODED_FILE = """
import ctypes, gc, sys
from pathlib import Path
from connectors.csv_files import read_page

def memory_mb():
    gc.collect()
    ctypes.CDLL("libc.so.6").malloc_trim(0)
    return int(Path("/proc/self/statm").read_text().split()[1]) * 4096 // 2**20

path, temp = Path(sys.argv[1]), Path(sys.argv[2])
read_page(path, "Windows-1252", ",", True, 0, 51, temp)
before = memory_mb()
for _ in range(20):
    read_page(path, "Windows-1252", ",", True, 0, 51, temp)
print(memory_mb() - before)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc and uses glibc")
def test_paging_a_decoded_file_does_not_keep_using_more_memory(tmp_path: Path) -> None:
    # Found reviewing T06: 240 pages of a 30 MB Windows-1252 file ended at 1 GB, from sniffing on
    # several threads through the Python filesystem. A separate process, for a clean measurement.
    path = tmp_path / "decoded.csv"
    path.write_bytes(b"id,price\n" + "Tea,€3\n".encode("cp1252") * (30 * 2**20 // 7))

    result = subprocess.run(
        [sys.executable, "-c", PAGING_A_DECODED_FILE, str(path), str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert int(result.stdout) < 50
