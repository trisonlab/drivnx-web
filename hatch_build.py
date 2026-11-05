"""What makes `uv build --wheel` the one build command for a release.

Every wheel gets the UI built from this checkout, and pins what running Conflux needs to `uv.lock`
(Section 7 of the direction doc), so installing the wheel gets the versions that were tested.
Installing from this folder doesn't: uv then reads the ranges in `pyproject.toml`, not these pins.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _command(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"Building the wheel needs {name}.")
    return found


class WheelBuild(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # An editable install runs from this source tree, and `uv sync` shouldn't need pnpm.
        if version == "editable":
            return
        root = Path(self.root)
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
        build_data["dependencies"] += pins.stdout.split("\n")[:-1]
