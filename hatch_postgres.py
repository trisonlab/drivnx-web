"""DuckDB's PostgreSQL extension, fetched when the wheel is built (ADR 0006).

DrivnX never downloads an extension, so the wheel carries this one. Only the standard library is
used, so the build hook and the tests can both load this file by its path.
"""

import gzip
import hashlib
import platform
import sys
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

NAME = "postgres_scanner.duckdb_extension"
REPOSITORY = "https://extensions.duckdb.org"
# The `.gz` files DuckDB's repository served for this version on 2026-11-13. A new DuckDB needs new
# hashes, and `check_locked_duckdb` stops a build that forgot them. They're public checksums, which
# a secret scanner takes for keys, hence the allowlist comments.
POSTGRES_EXTENSION = {
    "duckdb": "2.0.0",
    "sha256": {
        # pragma: allowlist nextline secret
        "linux_amd64": "54b3d751cafc9ffa439886b1607d0ee758dab4dc070f0171e779b95f2f3e554f",
        # pragma: allowlist nextline secret
        "osx_arm64": "496a338695582a99db2826597affc03307a4d7ed7b92c972e261adfe40f03fe3",
        # pragma: allowlist nextline secret
        "windows_amd64": "9b04ad866fd877836342f88913fe008b7e56bd2903dd013a5067f6becaf05d2b",
    },
}


def fetch_postgres_extension(
    platforms: Iterable[str],
    folder: Path,
    base_url: str,
    manifest: Mapping[str, Any],
    timeout: float = 60,
) -> None:
    """Download each platform's extension from DuckDB's repository and unpack it into `folder`.
    A repository that stops answering for `timeout` seconds fails the build, not hangs it."""
    for name in platforms:
        url = f"{base_url}/v{manifest['duckdb']}/{name}/{NAME}.gz"
        # DuckDB's repository refuses Python's own User-Agent (403), so a neutral one is sent.
        request = urllib.request.Request(url, headers={"User-Agent": "build"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            packed = response.read()
        # Before anything is written, so a changed file can't be left behind for a later build.
        if hashlib.sha256(packed).hexdigest() != manifest["sha256"][name]:
            raise RuntimeError(
                f"DuckDB's PostgreSQL extension for {name} doesn't match the hash pinned in"
                " hatch_postgres.py."
            )
        target = folder / name / NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        # Renamed into place, so an interrupted write can't leave a part that a later
        # `uv sync` takes for the whole file.
        part = target.with_name(f"{NAME}.part")
        part.write_bytes(gzip.decompress(packed))
        part.replace(target)


def check_locked_duckdb(pins: Iterable[str], manifest: Mapping[str, Any]) -> None:
    """Stop a build whose locked DuckDB isn't the one the extension was pinned for."""
    locked = next(pin.split("==", 1)[1] for pin in pins if pin.startswith("duckdb=="))
    if locked != manifest["duckdb"]:
        raise RuntimeError(
            f"uv.lock has DuckDB {locked}, but the PostgreSQL extension in hatch_postgres.py is"
            f" pinned for {manifest['duckdb']}. Pin the new version's hashes there."
        )


def this_platform() -> str:
    """DuckDB's name for the machine running the build, as `PRAGMA platform` gives it. A machine
    it can't name gets a name no extension is pinned for."""
    systems = {"linux": "linux", "darwin": "osx", "win32": "windows"}
    system = systems.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    machine = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )
    return f"{system}_{machine}"
