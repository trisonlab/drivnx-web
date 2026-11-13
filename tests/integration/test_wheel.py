"""The wheel a user installs, CFX-APP-first-slice AC-51."""

import os
import re
import subprocess
import tomllib
import zipfile
from email import message_from_bytes
from pathlib import Path

import duckdb
import pytest

from ..conftest import REPO_ROOT, run
from .test_lockfiles import _clean_checkout

UI = "local_api/ui"
BUILT_UI = f"apps/local-api/{UI}"


def test_the_command_comes_from_uv_tool_install(installed_environment: Path) -> None:
    # CFX-APP-first-slice/AC-51: the way a user gets it, so every test using it proves that too.
    listing = subprocess.run(
        ["uv", "tool", "list"],
        env={**os.environ, "UV_TOOL_DIR": str(installed_environment.parent)},
        capture_output=True,
        text=True,
        check=True,
    )

    assert "drivnx-data" in listing.stdout
    assert "- drivnx" in listing.stdout


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A clean checkout, as a user or CI would build from, holding a leftover UI file."""
    target = tmp_path_factory.mktemp("checkout")
    _clean_checkout(target)
    (target / BUILT_UI).mkdir(parents=True)
    (target / BUILT_UI / "left-over.js").write_text(
        "// from an older build\n",
        encoding="utf-8",
        newline="",
    )
    return target


@pytest.fixture(scope="module")
def built_wheel(checkout: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("built")
    run("uv", "build", "--wheel", "--out-dir", out, cwd=checkout)
    return next(out.glob("*.whl"))


@pytest.fixture(scope="module")
def wheel_names(built_wheel: Path) -> list[str]:
    with zipfile.ZipFile(built_wheel) as wheel:
        return wheel.namelist()


def test_building_the_wheel_builds_the_ui_into_it(wheel_names: list[str]) -> None:
    # CFX-APP-first-slice/AC-51 and the T01 review: this used to succeed with no UI at all.
    assert f"{UI}/index.html" in wheel_names
    assert any(name.startswith(f"{UI}/assets/") for name in wheel_names)


def test_the_wheel_carries_the_postgres_extension_for_each_tested_platform(
    wheel_names: list[str],
) -> None:
    # CFX-APP-postgres/AC-07 (ADR 0006): one wheel for every platform, and DrivnX downloads none.
    assert {
        name for name in wheel_names if name.startswith("connectors/postgres_extension/")
    } == {
        f"connectors/postgres_extension/{platform}/postgres_scanner.duckdb_extension"
        for platform in ("linux_amd64", "osx_arm64", "windows_amd64")
    }


def test_an_old_ui_bundle_doesnt_reach_the_wheel(wheel_names: list[str]) -> None:
    # The T01 review: this used to ship whatever an earlier build had left behind.
    assert f"{UI}/left-over.js" not in wheel_names


def build_in_clean_checkout(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path / "out")],
        cwd=tmp_path / "checkout",
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def clean_checkout(tmp_path: Path) -> Path:
    _clean_checkout(tmp_path / "checkout")
    return tmp_path / "checkout"


def test_a_ui_build_that_ends_up_elsewhere_stops_the_wheel_build(
    clean_checkout: Path, tmp_path: Path
) -> None:
    # The T01 review: the wheel would otherwise ship an older build's UI, or none at all.
    (clean_checkout / BUILT_UI).mkdir(parents=True)
    (clean_checkout / BUILT_UI / "index.html").write_text(
        "<p>from an older build</p>\n",
        encoding="utf-8",
        newline="",
    )
    config = clean_checkout / "apps/web/vite.config.ts"
    config.write_text(
        config.read_text(encoding="utf-8").replace("../local-api/local_api/ui", "moved"),
        encoding="utf-8",
        newline="",
    )

    result = build_in_clean_checkout(tmp_path)

    assert result.returncode != 0
    assert "index.html" in result.stderr
    assert not list((tmp_path / "out").glob("*.whl"))


def test_a_ui_that_doesnt_compile_stops_the_wheel_build(
    clean_checkout: Path, tmp_path: Path
) -> None:
    # The T01 review: otherwise the wheel holds the UI left by an earlier build.
    (clean_checkout / BUILT_UI).mkdir(parents=True)
    (clean_checkout / BUILT_UI / "index.html").write_text(
        "<p>from an older build</p>\n",
        encoding="utf-8",
        newline="",
    )
    main = clean_checkout / "apps/web/src/main.tsx"
    main.write_text(
        main.read_text(encoding="utf-8") + '\nconst broken: number = "text";\n',
        encoding="utf-8",
        newline="",
    )

    result = build_in_clean_checkout(tmp_path)

    assert result.returncode != 0
    assert "TS2322" in result.stdout + result.stderr
    assert not list((tmp_path / "out").glob("*.whl"))


def test_a_development_install_doesnt_build_the_ui(clean_checkout: Path) -> None:
    # CFX-APP-first-slice/AC-50 and the README: pnpm is for the UI, and uv alone sets up Python.
    run("uv", "sync", "--frozen", cwd=clean_checkout)

    assert not (clean_checkout / BUILT_UI).exists()
    assert not (clean_checkout / "apps/web/node_modules").exists()


def test_a_development_install_fetches_this_machines_postgres_extension(
    clean_checkout: Path,
) -> None:
    # CFX-APP-postgres/AC-07 (ADR 0006): tests and `uv run` load it from the source tree.
    platform = duckdb.execute("PRAGMA platform").fetchone()[0]

    run("uv", "sync", "--frozen", cwd=clean_checkout)

    fetched = clean_checkout / "packages/connectors/postgres_extension"
    assert [path.relative_to(fetched).as_posix() for path in fetched.rglob("*.*")] == [
        f"{platform}/postgres_scanner.duckdb_extension"
    ]


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_for_running() -> dict[str, str]:
    """What `uv.lock` says running DrivnX needs, walked from the project without the dev group."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    found: dict[str, str] = {}
    waiting = [dep["name"] for dep in packages["drivnx-data"]["dependencies"]]
    while waiting:
        name = waiting.pop()
        if name not in found:
            found[name] = packages[name]["version"]
            waiting += [dep["name"] for dep in packages[name].get("dependencies", [])]
    return found


def test_the_wheel_pins_what_running_drivnx_needs_to_the_lockfile(
    built_wheel: Path,
) -> None:
    # Section 7 of the direction doc: pinned dependencies, and the T01 review's open choice.
    with zipfile.ZipFile(built_wheel) as wheel:
        metadata_name = next(
            n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")
        )
        metadata = message_from_bytes(wheel.read(metadata_name))
    required = metadata.get_all("Requires-Dist")
    pins = {
        _normal(pin.group(1)): pin.group(2)
        for line in required
        if (pin := re.match(r"([\w.-]+)==([^;\s]+)", line))
    }

    assert pins == {
        _normal(name): version for name, version in _locked_for_running().items()
    }


def test_the_installed_versions_are_the_tested_ones(
    installed_environment: Path,
) -> None:
    # The T01 review: the install under test could otherwise pick versions newer than `uv.lock`.
    python = (
        installed_environment / ("Scripts" if os.name == "nt" else "bin") / "python"
    )
    listing = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import importlib.metadata as m\n"
                "for d in m.distributions(): print(d.metadata['Name'], d.version)"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    installed = dict(line.split() for line in listing.stdout.splitlines())
    installed = {_normal(name): version for name, version in installed.items()}
    del installed["drivnx-data"]

    assert installed == {
        _normal(name): version for name, version in _locked_for_running().items()
    }


def test_a_ui_lockfile_behind_its_package_stops_the_wheel_build(
    clean_checkout: Path, tmp_path: Path
) -> None:
    # Section 7 of the direction doc: the UI in a wheel is built from what's pinned.
    package = clean_checkout / "apps/web/package.json"
    package.write_text(
        package.read_text(encoding="utf-8").replace(
            '"dependencies": {', '"dependencies": {\n    "left-pad": "1.3.0",'
        ),
        encoding="utf-8",
        newline="",
    )

    result = build_in_clean_checkout(tmp_path)

    assert result.returncode != 0
    assert "OUTDATED_LOCKFILE" in result.stdout + result.stderr
    assert not list((tmp_path / "out").glob("*.whl"))


def test_a_lockfile_behind_the_project_stops_the_wheel_build(
    clean_checkout: Path, tmp_path: Path
) -> None:
    # It would otherwise pin a set that doesn't match what the project asks for.
    project = clean_checkout / "pyproject.toml"
    project.write_text(
        project.read_text(encoding="utf-8").replace('"fastapi>=', '"idna>=1",\n    "fastapi>='),
        encoding="utf-8",
        newline="",
    )

    result = build_in_clean_checkout(tmp_path)

    assert result.returncode != 0
    assert "uv.lock" in result.stdout + result.stderr
    assert not list((tmp_path / "out").glob("*.whl"))
