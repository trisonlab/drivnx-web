"""Loading DuckDB's PostgreSQL extension from inside the package, CFX-APP-postgres AC-07."""

import os
import subprocess
from pathlib import Path

import duckdb
import pytest

from connectors import postgres
from connectors.csv_files import duckdb_connection

THIS_MACHINE = duckdb.execute("PRAGMA platform").fetchone()[0]


def test_the_bundled_extension_is_found_for_this_machine_and_not_for_others() -> None:
    # CFX-APP-postgres/AC-07: only the tested platforms carry one (ADR 0006).
    found = postgres.extension_file(THIS_MACHINE)

    assert found is not None
    assert found.name == "postgres_scanner.duckdb_extension"
    assert Path(postgres.__file__).parent in found.parents
    assert postgres.extension_file("linux_arm64") is None


def no_way_to_download(tmp_path: Path, closed_port: int) -> duckdb.DuckDBPyConnection:
    """A connection where every way DuckDB has to fetch an extension fails."""
    return duckdb.connect(
        config={
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
            "extension_directory": str(tmp_path / "empty"),
            "custom_extension_repository": f"http://127.0.0.1:{closed_port}",
        }
    )


def test_the_extension_loads_from_the_package_with_no_way_to_download(
    tmp_path: Path, free_port: int
) -> None:
    # CFX-APP-postgres/AC-07: DrivnX downloads no extension (ADR 0006).
    with pytest.raises(duckdb.Error):
        no_way_to_download(tmp_path, free_port).execute("INSTALL postgres")
    connection = no_way_to_download(tmp_path, free_port)

    postgres.load_postgres(connection)

    assert connection.execute(
        "SELECT count(*) FROM duckdb_functions() WHERE function_name = 'postgres_query'"
    ).fetchone() == (1,)


def test_a_changed_extension_is_refused_by_drivnxs_duckdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-postgres/AC-07: DuckDB checks the signature of what it loads, so a file changed after
    # the build, in the package or on disk, can't run inside DrivnX.
    bundled = postgres.extension_file(THIS_MACHINE)
    changed = tmp_path / "bundle" / THIS_MACHINE / bundled.name
    changed.parent.mkdir(parents=True)
    content = bytearray(bundled.read_bytes())
    content[len(content) // 2] ^= 0xFF
    changed.write_bytes(content)
    monkeypatch.setattr(postgres, "EXTENSIONS", tmp_path / "bundle")

    with duckdb_connection(tmp_path / "spill") as connection:
        with pytest.raises(duckdb.Error, match="signature"):
            postgres.load_postgres(connection)


def test_a_machine_with_no_bundled_extension_is_told_so_by_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-postgres/AC-07: on another platform, PostgreSQL isn't available (the screen is
    # DRIVN-47's), rather than DuckDB failing on a path that isn't there.
    monkeypatch.setattr(postgres, "EXTENSIONS", tmp_path / "nothing bundled")

    with duckdb_connection(tmp_path / "spill") as connection:
        with pytest.raises(postgres.PostgresUnavailable, match=THIS_MACHINE):
            postgres.load_postgres(connection)


INSTALLED_LOAD = """
import sys
import duckdb
from connectors.postgres import load_postgres

connection = duckdb.connect(config={
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
    "extension_directory": sys.argv[1],
    "custom_extension_repository": sys.argv[2],
})
load_postgres(connection)
print(connection.execute(
    "SELECT count(*) FROM duckdb_functions() WHERE function_name = 'postgres_query'"
).fetchone()[0])
if len(sys.argv) > 3:
    connection.execute(f"ATTACH '{sys.argv[3]}' AS fixture (TYPE postgres, READ_ONLY)")
    print(connection.execute("SELECT * FROM fixture.public.people ORDER BY id").fetchall())
"""


def test_the_installed_wheel_loads_its_own_extension(
    installed_environment: Path, tmp_path: Path, free_port: int
) -> None:
    # CFX-APP-postgres/AC-07: on each tested platform, from the wheel a user installs.
    python = installed_environment / ("Scripts" if os.name == "nt" else "bin") / "python"

    result = subprocess.run(
        [
            str(python),
            "-c",
            INSTALLED_LOAD,
            str(tmp_path / "empty"),
            f"http://127.0.0.1:{free_port}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_the_installed_wheel_reads_a_postgres_database_with_no_way_to_download(
    installed_environment: Path, tmp_path: Path, free_port: int, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-07: when DrivnX connects, the bundled extension does the work, and DuckDB
    # has no other host it could fetch anything from.
    python = installed_environment / ("Scripts" if os.name == "nt" else "bin") / "python"

    result = subprocess.run(
        [
            str(python),
            "-c",
            INSTALLED_LOAD,
            str(tmp_path / "empty"),
            f"http://127.0.0.1:{free_port}",
            postgres_server,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "1",
        "[(1, 'Ann', datetime.date(2024, 1, 5)), (2, 'Bob', None),"
        " (3, 'Zoé', datetime.date(2024, 3, 17))]",
    ]
