"""What makes `uv build --wheel` the one build command for a release.

Every wheel gets the UI built from this checkout, and pins what running DrivnX needs to `uv.lock`
(Section 7 of the direction doc), so installing the wheel gets the versions that were tested. It
also carries DuckDB's PostgreSQL extension for each tested platform, checked against pinned hashes
(ADR 0006), as DrivnX itself never downloads one.
Installing from this folder doesn't: uv then reads the ranges in `pyproject.toml`, not these pins.
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _postgres_fetcher(root: Path):
    """`hatch_postgres.py`, loaded by its path: the build runs with only hatchling installed."""
    spec = importlib.util.spec_from_file_location("hatch_postgres", root / "hatch_postgres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _command(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"Building the wheel needs {name}.")
    return found


class WheelBuild(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        extension = root / "packages" / "connectors" / "postgres_extension"
        # An editable install runs from this source tree, and `uv sync` shouldn't need pnpm. It
        # needs this machine's PostgreSQL extension, though, for tests and `uv run` (ADR 0006).
        if version == "editable":
            postgres = _postgres_fetcher(root)
            platform = postgres.this_platform()
            if platform in postgres.POSTGRES_EXTENSION["sha256"] and not (
                extension / platform / postgres.NAME
            ).is_file():
                postgres.fetch_postgres_extension(
                    [platform], extension, postgres.REPOSITORY, postgres.POSTGRES_EXTENSION
                )
            return
        pnpm = _command("pnpm")
        web = root / "apps" / "web"
        subprocess.run([pnpm, "install", "--frozen-lockfile"], cwd=web, check=True)
        # An older build's files would otherwise pass the check below, and ship.
        bundle = root / "apps" / "local-api" / "local_api" / "ui"
        shutil.rmtree(bundle, ignore_errors=True)
        subprocess.run([pnpm, "build"], cwd=web, check=True)
        if not (bundle / "index.html").is_file():
            raise RuntimeError(
                "The UI build left no index.html where the wheel picks it up."
            )
        # --locked, as a lockfile behind pyproject.toml would otherwise pin the wrong set.
        pins = subprocess.run(
            [
                _command("uv"),
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--no-header",
                "--no-annotate",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        locked = pins.stdout.split("\n")[:-1]
        build_data["dependencies"] += locked
        # One wheel for every tested platform (ADR 0006). Emptied first, as with the UI.
        postgres = _postgres_fetcher(root)
        postgres.check_locked_duckdb(locked, postgres.POSTGRES_EXTENSION)
        shutil.rmtree(extension, ignore_errors=True)
        postgres.fetch_postgres_extension(
            postgres.POSTGRES_EXTENSION["sha256"],
            extension,
            postgres.REPOSITORY,
            postgres.POSTGRES_EXTENSION,
        )
