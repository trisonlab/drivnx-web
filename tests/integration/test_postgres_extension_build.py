"""Building DuckDB's PostgreSQL extension into the wheel, CFX-APP-postgres AC-07 (ADR 0006)."""

import gzip
import hashlib
import runpy
import socket
import threading
import tomllib
from pathlib import Path

import pytest

from ..conftest import REPO_ROOT

FETCH = runpy.run_path(str(REPO_ROOT / "hatch_postgres.py"))
NAME = "postgres_scanner.duckdb_extension"


def served(tmp_path: Path, version: str, platform: str, content: bytes) -> tuple[str, str]:
    """Serve `content` as DuckDB's repository would, and return its base URL and the hash a
    manifest would pin for it."""
    gz = tmp_path / "repository" / f"v{version}" / platform / f"{NAME}.gz"
    gz.parent.mkdir(parents=True)
    gz.write_bytes(gzip.compress(content))
    return (tmp_path / "repository").as_uri(), hashlib.sha256(gz.read_bytes()).hexdigest()


def test_a_download_matching_its_hash_is_unpacked_for_its_platform(tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-07: the file DrivnX loads is the one whose hash was pinned.
    base, digest = served(tmp_path, "9.9.9", "linux_amd64", b"an extension")
    manifest = {"duckdb": "9.9.9", "sha256": {"linux_amd64": digest}}

    FETCH["fetch_postgres_extension"](["linux_amd64"], tmp_path / "out", base, manifest)

    assert (tmp_path / "out" / "linux_amd64" / NAME).read_bytes() == b"an extension"


def test_a_download_not_matching_its_hash_stops_the_build_and_writes_nothing(
    tmp_path: Path,
) -> None:
    # CFX-APP-postgres/AC-07: a changed file on the server, or in transit, must not ship.
    base, digest = served(tmp_path, "9.9.9", "osx_arm64", b"what was pinned")
    gz = tmp_path / "repository" / "v9.9.9" / "osx_arm64" / f"{NAME}.gz"
    gz.write_bytes(gzip.compress(b"something else"))
    manifest = {"duckdb": "9.9.9", "sha256": {"osx_arm64": digest}}

    with pytest.raises(RuntimeError, match="osx_arm64"):
        FETCH["fetch_postgres_extension"](["osx_arm64"], tmp_path / "out", base, manifest)

    assert not (tmp_path / "out").exists()


def test_the_pinned_extension_is_for_the_locked_duckdb_on_each_tested_platform() -> None:
    # CFX-APP-postgres/AC-07: an extension built for another DuckDB version won't load.
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = next(p["version"] for p in lock["package"] if p["name"] == "duckdb")
    manifest = FETCH.get("POSTGRES_EXTENSION", {})

    assert manifest.get("duckdb") == locked
    assert sorted(manifest.get("sha256", {})) == ["linux_amd64", "osx_arm64", "windows_amd64"]


def test_a_locked_duckdb_the_extension_wasnt_pinned_for_stops_the_build() -> None:
    # CFX-APP-postgres/AC-07: upgrading DuckDB alone would otherwise ship an extension it refuses.
    manifest = {"duckdb": "2.0.0", "sha256": {}}
    check = FETCH.get("check_locked_duckdb", lambda pins, manifest: None)

    check(["fastapi==0.162.0", "duckdb==2.0.0"], manifest)
    with pytest.raises(RuntimeError, match="2.0.1"):
        check(["fastapi==0.162.0", "duckdb==2.0.1"], manifest)


def test_a_repository_that_never_answers_fails_the_fetch_instead_of_hanging(
    tmp_path: Path,
) -> None:
    # The DRIVN-46 review: a stalled download would otherwise hang `uv sync`, every wheel build and
    # CI until its job limit.
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen()
    base = f"http://127.0.0.1:{silent.getsockname()[1]}"
    manifest = {"duckdb": "9.9.9", "sha256": {"linux_amd64": "unused"}}
    failed: list[BaseException] = []

    def fetch() -> None:
        try:
            FETCH["fetch_postgres_extension"](
                ["linux_amd64"], tmp_path / "out", base, manifest, timeout=1
            )
        except Exception as error:
            failed.append(error)

    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(10)
    silent.close()

    assert not worker.is_alive()
    assert len(failed) == 1 and isinstance(failed[0], OSError)
    assert not (tmp_path / "out").exists()
