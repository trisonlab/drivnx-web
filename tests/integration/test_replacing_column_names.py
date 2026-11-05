"""Earlier column names leave the workspace after a correction, CFX-APP-csv-profile AC-04."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

PLANTED = b"Zanzibar"


def connect(client: TestClient, path: Path) -> dict:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 201, response.text
    return response.json()


def save(client: TestClient, source: dict, **changes: object) -> dict:
    settings = {
        "delimiter": source["delimiter"],
        "encoding": source["encoding"],
        "has_header": source["has_header"],
        "skip_lines": source["skip_lines"],
        **changes,
    }
    response = client.put(f"/api/sources/{source['id']}/settings", json=settings)
    assert response.status_code == 200, response.text
    return response.json()


def raw_bytes(workspace_dir: Path) -> bytes:
    """The database as anyone copying the files would get it, with freed space and the WAL."""
    database = workspace_dir / "workspace.db"
    wal = workspace_dir / "workspace.db-wal"
    return database.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def field_rows(workspace_dir: Path) -> list[tuple[str, str, int, str]]:
    database = sqlite3.connect(workspace_dir / "workspace.db")
    try:
        return database.execute(
            "SELECT id, dataset_id, position, name FROM field ORDER BY rowid"
        ).fetchall()
    finally:
        database.close()


def test_a_row_used_as_column_names_is_gone_after_the_correction(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-04
    path = tmp_path / "people.csv"
    path.write_text("ZanzibarAnn,ZanzibarParis\nBob,Lyon\nCid,Nice\nDee,Metz\n")
    source = connect(client, path)
    assert source["has_header"] is True
    assert PLANTED in raw_bytes(workspace_dir)

    corrected = save(client, source, has_header=False)

    assert [column["name"] for column in corrected["columns"]] == ["column0", "column1"]
    assert PLANTED not in raw_bytes(workspace_dir)


def test_earlier_datasets_get_generated_names_and_keep_their_ids(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-04: evidence points at a field's id, so that mustn't change.
    path = tmp_path / "people.csv"
    path.write_text("ZanzibarAnn,ZanzibarParis\nBob,Lyon\nCid,Nice\nDee,Metz\n")
    source = connect(client, path)
    before = field_rows(workspace_dir)

    save(client, source, has_header=False)

    after = field_rows(workspace_dir)
    assert [row[0] for row in after[: len(before)]] == [row[0] for row in before]
    assert [row[3] for row in after] == ["column0", "column1", "column0", "column1"]


def test_names_from_every_earlier_correction_are_gone(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-04
    path = tmp_path / "report.csv"
    path.write_text(
        "ZanzibarTitle;x\nZanzibarLine,y\nZanzibarHeader,z\nBob,Lyon\nCid,Nice\nDee,Metz\n"
    )
    source = connect(client, path)

    first = save(client, source, delimiter=",", has_header=True, skip_lines=1)
    assert first["columns"][0]["name"] == "ZanzibarLine"
    second = save(client, first, skip_lines=2)
    assert second["columns"][0]["name"] == "ZanzibarHeader"
    assert PLANTED in raw_bytes(workspace_dir)
    save(client, second, has_header=False, skip_lines=3)

    assert PLANTED not in raw_bytes(workspace_dir)


def test_a_wide_file_leaves_nothing_behind_in_any_page(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-04: names this long fill many pages, and pages get rearranged as other
    # sources are saved around them.
    wide = tmp_path / "wide.csv"
    header = ",".join(f"Zanzibar{number:04}{'x' * 200}" for number in range(400))
    row = ",".join(f"row{number}" for number in range(400))
    wide.write_text(f"{header}\n{row}\n{row}\n")
    source = connect(client, wide)
    for number in range(5):
        other = tmp_path / f"other{number}.csv"
        other.write_text(",".join(f"c{n}{'y' * 200}" for n in range(200)) + "\n1\n")
        connect(client, other)

    save(client, source, has_header=False)

    assert PLANTED not in raw_bytes(workspace_dir)


def test_a_correction_that_keeps_the_names_keeps_them_on_earlier_runs(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-04: nothing here came from a data row, and earlier runs stay readable.
    path = tmp_path / "people.csv"
    path.write_bytes("id,name\n1,Zoé\n".encode())
    source = connect(client, path)

    save(client, source, encoding="Latin-1")

    assert [row[3] for row in field_rows(workspace_dir)] == ["id", "name", "id", "name"]


def test_other_sources_keep_their_names(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-04
    corrected = tmp_path / "people.csv"
    corrected.write_text("Ann,Paris\nBob,Lyon\nCid,Nice\nDee,Metz\n")
    source = connect(client, corrected)
    other = tmp_path / "other.csv"
    other.write_text("ZanzibarId,ZanzibarName\n1,Ann\n")
    connect(client, other)

    save(client, source, has_header=False)

    assert PLANTED in raw_bytes(workspace_dir)
    assert client.get("/api/sources").json()[1]["columns"][0]["name"] == "ZanzibarId"


def test_another_sources_earlier_names_are_left_alone(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-04
    other = tmp_path / "other.csv"
    other.write_bytes("id,name\n1,Zoé\n".encode())
    save(client, connect(client, other), encoding="Latin-1")
    path = tmp_path / "people.csv"
    path.write_text("Ann,Paris\nBob,Lyon\nCid,Nice\nDee,Metz\n")

    save(client, connect(client, path), has_header=False)

    assert [row[3] for row in field_rows(workspace_dir)][:4] == [
        "id",
        "name",
        "id",
        "name",
    ]
