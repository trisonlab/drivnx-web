"""Writes the synthetic CSV files in testdata/csv. Everything in them is invented.

Run with `uv run python testdata/generate_csv.py`. CFX-APP-first-slice/AC-54
"""

from pathlib import Path

CSV_DIR = Path(__file__).parent / "csv"
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


def write_all(directory: Path = CSV_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in FILES.items():
        (directory / name).write_bytes(contents)


if __name__ == "__main__":
    write_all()
