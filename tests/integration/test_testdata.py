"""Where everything in testdata/ comes from, CFX-APP-first-slice AC-54."""

import filecmp
import runpy
import subprocess
from pathlib import Path

from ..conftest import REPO_ROOT

TESTDATA = REPO_ROOT / "testdata"
GENERATOR = TESTDATA / "generate_csv.py"


def _files_in(directory: Path) -> set[str]:
    return {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def test_the_generator_makes_every_csv_exactly(tmp_path: Path) -> None:
    # CFX-APP-first-slice/AC-54: a file nobody can regenerate could hold anything.
    runpy.run_path(str(GENERATOR))["write_all"](tmp_path)

    committed = TESTDATA / "csv"
    assert _files_in(tmp_path) == _files_in(committed)
    _, different, errors = filecmp.cmpfiles(
        tmp_path, committed, _files_in(tmp_path), shallow=False
    )
    assert different == []
    assert errors == []


def test_the_generator_makes_every_golden_file_exactly(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-40: golden files are synthetic and can be regenerated.
    runpy.run_path(str(GENERATOR))["write_golden"](tmp_path)

    committed = TESTDATA / "golden"
    assert _files_in(tmp_path) == _files_in(committed)
    _, different, errors = filecmp.cmpfiles(
        tmp_path, committed, _files_in(tmp_path), shallow=False
    )
    assert different == []
    assert errors == []


def test_git_leaves_the_csvs_line_endings_alone() -> None:
    # A checkout with core.autocrlf on, usual on Windows, would change 24 of these files' bytes.
    listing = subprocess.run(
        ["git", "check-attr", "text", "--", "testdata/csv/comma_utf8.csv"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert listing.stdout.strip().endswith("text: unset")


def test_nothing_else_is_in_testdata() -> None:
    # CFX-APP-first-slice/AC-54: nothing is hand-written today, so anything new has to say so here.
    outside_the_csvs = {
        name
        for name in _files_in(TESTDATA)
        if not name.startswith(("csv/", "golden/"))
    }

    assert outside_the_csvs == {"generate_csv.py"}
