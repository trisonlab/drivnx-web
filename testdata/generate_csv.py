"""Writes the synthetic CSV files in testdata/csv and testdata/golden. Everything in them is invented.

Run with `uv run python testdata/generate_csv.py`. CFX-APP-first-slice/AC-54, CFX-APP-csv-profile/AC-40
"""

from pathlib import Path

CSV_DIR = Path(__file__).parent / "csv"
GOLDEN_DIR = Path(__file__).parent / "golden"
BOM_UTF8 = b"\xef\xbb\xbf"
BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF16_BE = b"\xfe\xff"

CUSTOMERS = [
    ("id", "name", "city", "amount", "joined"),
    ("1", "Ann", "Paris", "10.50", "2024-01-15"),
    ("2", "Bob", "Lyon", "7.25", "2024-02-20"),
    ("3", "Zoé", "Orléans", "3.00", "2024-03-05"),
    ("4", "José", "Málaga", "12.75", "2024-04-09"),
]
PRICES = [
    ("item", "price", "note"),
    ("Café", "€5", "“fresh”"),
    ("Thé", "€3", "it’s hot"),
]
ADDRESSES = [
    ("id", "address"),
    ("1", "1 Main St, Paris, France"),
    ("2", "2 High St, Lyon, France"),
    ("3", "3 Low St, Nice, France"),
    ("4", "4 Long St, Metz, France"),
]
EMOJI = [
    ("id", "note"),
    ("1", "😀 great"),
    ("2", "🎉 party"),
]
AMOUNTS = [
    ("id", "name", "amount"),
    ("1", "Ann", "10.50"),
    ("2", "Bob", "7.25"),
    ("3", "Cid", "3.00"),
    ("4", "Dee", "12.75"),
]


def table(rows: list[tuple[str, ...]], delimiter: str = ",", newline: str = "\n") -> str:
    return "".join(delimiter.join(row) + newline for row in rows)


FILES: dict[str, bytes] = {
    "comma_utf8.csv": table(CUSTOMERS).encode(),
    "semicolon_utf8.csv": table(CUSTOMERS, ";").encode(),
    "tab_utf8.csv": table(CUSTOMERS, "\t").encode(),
    "pipe_utf8.csv": table(CUSTOMERS, "|").encode(),
    "comma_utf8_bom.csv": BOM_UTF8 + table(CUSTOMERS).encode(),
    "comma_utf16_le.csv": BOM_UTF16_LE + table(CUSTOMERS).encode("utf-16-le"),
    "comma_utf16_be.csv": BOM_UTF16_BE + table(CUSTOMERS).encode("utf-16-be"),
    "emoji_utf16_le.csv": BOM_UTF16_LE + table(EMOJI).encode("utf-16-le"),
    "emoji_utf16_be.csv": BOM_UTF16_BE + table(EMOJI).encode("utf-16-be"),
    "comma_latin1.csv": table(CUSTOMERS).encode("latin-1"),
    "prices_windows1252.csv": table(PRICES).encode("cp1252"),
    "no_header_typed.csv": table(CUSTOMERS[1:]).encode(),
    "no_header_all_text.csv": table([("Ann", "Paris"), ("Bob", "Lyon"), ("Zoé", "Nice")]).encode(),
    "header_all_text.csv": table([("name", "city"), ("Ann", "Paris"), ("Bob", "Lyon")]).encode(),
    "crlf_utf8.csv": table(CUSTOMERS, newline="\r\n").encode(),
    "one_column.csv": table([("id",), ("1",), ("2",), ("3",)]).encode(),
    "quoted_delimiter.csv": b'id,note\n1,"a, b"\n2,"c, d"\n',
    "header_only.csv": b"id,name\n",
    "tab_commas_in_text.csv": table(ADDRESSES, "\t").encode(),
    "semicolon_commas_in_text.csv": table(ADDRESSES, ";").encode(),
    "pipe_commas_in_text.csv": table(ADDRESSES, "|").encode(),
    "extra_field_in_a_row.csv": (table(AMOUNTS) + "5,Eve,1.00,surplus\n" + table(AMOUNTS[1:])).encode(),
    "short_row.csv": (table(AMOUNTS) + "5\n" + table(AMOUNTS[1:])).encode(),
    "no_header_commas_in_text.csv": table(ADDRESSES[1:], "\t").encode(),
    "title_line.csv": ("Sales report\n" + table(AMOUNTS)).encode(),
    "one_column_commas_in_text.csv": table([("address",)] + [(row[1],) for row in ADDRESSES[1:]]).encode(),
    "title_line_tab_commas_in_text.csv": ("Sales report\n" + table(ADDRESSES, "\t")).encode(),
    "header_one_field_short.csv": table(
        [("name", "city"), ("Ann", "Paris", "France"), ("Bob", "Lyon", "France"), ("Cid", "Nice", "France")]
    ).encode(),
    "no_header_first_row_fits_no_type.csv": table(
        [("Ann", "N/A", "2024-01-15"), ("Bob", "5", "2024-01-16"), ("Cid", "6", "2024-01-17")], "\t"
    ).encode(),
    "empty.csv": b"",
    "utf16_no_bom.csv": table(CUSTOMERS).encode("utf-16-le"),
    "binary.csv": bytes(range(256)) * 40,
}


# tests/golden/test_metrics.py holds what each of these should measure, worked out by hand.
GOLDEN = {
    "missing_values.csv": b"id,name,note\n1,Ann,\n2,,\n3,   ,\n4,Dee,\n",
    "duplicates.csv": (
        b"id,city,code\n1,Paris,X1\n2,Paris,X1\n3,Lyon,X1\n4,Paris,X1\n5,Lyon,Y2\n"
    ),
    "dates_iso.csv": (
        b"id,joined,stamp\n"
        b"1,2024-01-15,2024-01-15 10:00:00\n"
        b"2,2024-02-29,2024-02-29 11:30:00\n"
        b"3,2024-12-31,2024-12-31 23:59:59\n"
    ),
    "dates_us.csv": b"id,joined\n1,03/25/2024\n2,12/31/2024\n3,01/15/2024\n",
    "dates_two_formats.csv": (
        b"id,first,second\n"
        b"1,2024-03-25,2024/03/25\n"
        b"2,2024-03-26,2024/03/26\n"
        b"3,2024-03-27,2024/03/27\n"
    ),
    "timestamps_two_formats.csv": (
        b"id,local,utc\n"
        b"1,25/03/2024 10:00:00,2024-03-25 09:00:00\n"
        b"2,26/03/2024 10:00:00,2024-03-26 09:00:00\n"
        b"3,27/03/2024 10:00:00,2024-03-27 09:00:00\n"
    ),
    "large_integers.csv": (
        b"id,balance,ref\n"
        b"1,-9345230074912938,65573643442266533278\n"
        b"2,-9253028069769886,65573643442266533279\n"
        b"3,-9066624320668406,65573643442266533277\n"
        b"4,-5,7\n"
    ),
    "unicode.csv": (
        "id,name,price\n"
        "1,Zo\u00e9,\u0663\u0664\n"
        "2,\u674e\u660e,\uff13\uff14\n"
        "3,\u0418\u0432\u0430\u043d,12\n"
        "4,Zoe\u0301,\u00bd\n"
    ).encode(),
    "mixed_types.csv": (
        "id,value\n"
        + "".join(f"{row},7\n" for row in range(20_600))
        + "20600,1.5\n20601,abc\n20602,12\n20603,\n"
    ).encode(),
    "high_cardinality.csv": (
        "id,code,group\n"
        + "".join(f"{row},C{row:04},g{row % 3}\n" for row in range(1_000))
    ).encode(),
}


def write_golden(directory: Path = GOLDEN_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in GOLDEN.items():
        (directory / name).write_bytes(contents)


def write_all(directory: Path = CSV_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in FILES.items():
        (directory / name).write_bytes(contents)


if __name__ == "__main__":
    write_all()
    write_golden()
