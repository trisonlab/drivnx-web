"""CFX-APP-first-slice/AC-50: a clean checkout installs from the lockfiles without changing them."""

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCKFILES = ["uv.lock", "apps/web/pnpm-lock.yaml"]


def _clean_checkout(target: Path) -> None:
    """Copy what a checkout would contain: tracked and new files, but nothing git ignores."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for name in filter(None, listing.stdout.split("\0")):
        source = REPO_ROOT / name
        if source.is_file():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def test_frozen_install_succeeds_and_leaves_the_lockfiles_unchanged(
    tmp_path: Path,
) -> None:
    _clean_checkout(tmp_path)
    before = {name: (tmp_path / name).read_bytes() for name in LOCKFILES}

    subprocess.run(
        ["uv", "sync", "--frozen"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["uv", "lock", "--check"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["pnpm", "install", "--frozen-lockfile"],
        cwd=tmp_path / "apps/web",
        check=True,
        capture_output=True,
    )

    assert {name: (tmp_path / name).read_bytes() for name in LOCKFILES} == before
