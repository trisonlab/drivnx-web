"""What a masked pattern keeps of a value, CFX-APP-csv-profile AC-14 and AC-20, from ADR 0004."""

import csv
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def patterns_of(client: TestClient, tmp_path: Path, values: list[str]) -> list[dict]:
    """A real run's patterns for a column holding these values."""
    path = tmp_path / "values.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("id", "value"))
        writer.writerows((number, value) for number, value in enumerate(values))
    connected = client.post("/api/sources", json={"path": str(path)})
    assert connected.status_code == 201, connected.text
    assert connected.json()["columns"][1]["name"] == "value"
    started = client.post(
        "/api/profile-runs", json={"source_id": connected.json()["id"], "mode": "fast"}
    )
    assert started.status_code == 202, started.text
    deadline = time.monotonic() + 30
    while True:
        run = client.get(f"/api/profile-runs/{started.json()['id']}").json()
        if run["status"] != "running":
            break
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert run["status"] == "completed"
    found = [
        metric["value"]
        for metric in run["metrics"]
        if metric["column"] == "value" and metric["metric"] == "masked_patterns"
    ]
    return found[0] if found else []


@pytest.mark.parametrize(
    ("value", "pattern"),
    [
        ("Zoe", "AAA"),
        ("Иван", "AAAA"),
        ("سارة", "AAAA"),
        ("דנה", "AAA"),
        ("李明", "AA"),
        ("민수", "AA"),
        ("12", "99"),
        ("٣٤", "99"),
        ("３４", "99"),
        ("४२", "99"),
        ("a-1 b_2!", "A-9 A_9!"),
        ("ann@example.org", "AAA@AAAAAAA.AAA"),
        ("\N{GRINNING FACE}x", "\N{GRINNING FACE}A"),
    ],
)
def test_letters_become_a_and_digits_become_9_in_any_script_and_the_rest_stays(
    client: TestClient, tmp_path: Path, value: str, pattern: str
) -> None:
    # CFX-APP-csv-profile/AC-20
    found = patterns_of(client, tmp_path, [value, value, value])

    assert found == [{"pattern": pattern, "count": 3, "cut": False}]


@pytest.mark.parametrize(
    "value",
    ["\U0002EBF0", "\U00010D50", "\U00011380", "\U0001CCF0"],
    ids=["cjk_extension_i", "garay", "tulu_tigalari", "digit"],
)
def test_a_character_too_new_for_duckdb_is_still_hidden(
    client: TestClient, tmp_path: Path, value: str
) -> None:
    # CFX-APP-csv-profile/AC-20: found reviewing T05. Its tables don't know these, so asking for
    # letters misses them and the saved pattern would be the value itself. A too-new digit can't
    # be told from a letter, so it becomes `A`, which hides it just the same.
    assert patterns_of(client, tmp_path, [value] * 3)[0]["pattern"] == "A"


@pytest.mark.parametrize(
    ("value", "pattern"),
    [
        ("Zoe\u0301", "AAA"),
        ("Zoé", "AAA"),
        ("राम", "AA"),
        ("สมชาย", "AAAAA"),
    ],
    ids=["decomposed", "composed", "devanagari", "thai"],
)
def test_a_combining_mark_counts_as_part_of_its_letter(
    client: TestClient, tmp_path: Path, value: str, pattern: str
) -> None:
    # CFX-APP-csv-profile/AC-20, by the user's decision: `é` gives one `A` however it's encoded,
    # and a vowel sign on a letter doesn't stay.
    assert patterns_of(client, tmp_path, [value] * 3)[0]["pattern"] == pattern


@pytest.mark.parametrize("value", ["½", "Ⅷ", "²"])
def test_a_number_that_is_not_a_decimal_digit_becomes_9_too(
    client: TestClient, tmp_path: Path, value: str
) -> None:
    # CFX-APP-csv-profile/AC-20, by the user's decision: left alone, the saved pattern would be the
    # value itself, which ADR 0004 is there to prevent.
    assert patterns_of(client, tmp_path, [value] * 3)[0]["pattern"] == "9"


@pytest.mark.parametrize(
    ("length", "pattern_length", "cut"),
    [(39, 39, False), (40, 40, False), (41, 40, True), (5_000, 40, True)],
)
def test_a_pattern_stops_at_40_characters_and_says_when_it_was_cut(
    client: TestClient, tmp_path: Path, length: int, pattern_length: int, cut: bool
) -> None:
    # CFX-APP-csv-profile/AC-20
    found = patterns_of(client, tmp_path, ["x" * length] * 3)

    assert found == [{"pattern": "A" * pattern_length, "count": 3, "cut": cut}]


def test_only_a_cut_pattern_is_marked_when_both_look_the_same(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-20: 40 letters and 41 letters are different shapes.
    found = patterns_of(client, tmp_path, ["x" * 40] * 2 + ["y" * 41])

    assert found == [
        {"pattern": "A" * 40, "count": 2, "cut": False},
        {"pattern": "A" * 40, "count": 1, "cut": True},
    ]


def test_a_column_keeps_its_10_most_common_patterns(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14, from ADR 0004. 12 shapes: length 1 appears 12 times, length 12 once.
    values = [
        "x" * length for length in range(1, 13) for _ in range(13 - length)
    ]

    found = patterns_of(client, tmp_path, values)

    assert [(entry["pattern"], entry["count"]) for entry in found] == [
        ("A" * length, 13 - length) for length in range(1, 11)
    ]


def test_equally_common_patterns_come_in_a_repeatable_order(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-19: a golden file couldn't hold an exact value otherwise.
    found = patterns_of(client, tmp_path, ["b1", "1b", "b1", "1b", "bb", "bb"])

    assert [entry["pattern"] for entry in found] == ["9A", "A9", "AA"]


def test_missing_values_have_no_pattern(client: TestClient, tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-14: a blank isn't a shape of the column's values.
    found = patterns_of(client, tmp_path, ["ab", "", "   ", " ", "cd"])

    assert found == [{"pattern": "AA", "count": 2, "cut": False}]


def test_a_column_with_nothing_in_it_saves_no_patterns(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-14
    assert patterns_of(client, tmp_path, ["", "  ", ""]) == []


def test_a_pattern_is_all_that_is_saved_of_a_value(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # The R3 rule, from ADR 0004. T12 proves this for the whole journey.
    found = patterns_of(client, tmp_path, ["Quokka-918273645"] * 3)

    assert found[0]["pattern"] == "AAAAAA-999999999"
    wal = workspace_dir / "workspace.db-wal"
    saved = (workspace_dir / "workspace.db").read_bytes() + wal.read_bytes()
    assert b"AAAAAA-999999999" in saved
    assert b"Quokka" not in saved
    assert b"918273645" not in saved


def test_only_the_start_of_a_long_value_is_masked(client: TestClient, tmp_path: Path) -> None:
    # A cell's masking costs time by its length, and 100,000 cells of 1,000 characters took 28 s.
    # A value with several marks on every letter gets a shorter pattern, still marked as cut.
    value = "x́́́" * 50

    found = patterns_of(client, tmp_path, [value] * 3)

    assert found == [{"pattern": "A" * 25, "count": 3, "cut": True}]
