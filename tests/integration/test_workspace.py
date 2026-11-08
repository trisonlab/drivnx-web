"""Opening a workspace safely, CFX-APP-first-slice AC-02, AC-03, AC-04, AC-17, AC-18, AC-19, AC-44."""

import signal
import stat
from pathlib import Path

import pytest

from ..conftest import StartDrivnX, posix_only


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@posix_only
def test_a_non_empty_directory_that_is_not_a_workspace_is_refused_and_left_alone(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-03
    documents = tmp_path / "Documents"
    documents.mkdir(mode=0o755)
    documents.chmod(0o755)
    (documents / "taxes.csv").write_text("a,b\n")
    before = sorted(entry.name for entry in documents.iterdir())

    drivnx = start_drivnx("--no-browser", workspace=documents)

    assert drivnx.process.wait(timeout=10) == 1
    assert "subdirectory" in drivnx.all_output()
    assert sorted(entry.name for entry in documents.iterdir()) == before
    assert mode(documents) == 0o755


@pytest.mark.parametrize(
    "state", ["new", "empty", "only a lock file", "crashed first launch"]
)
def test_a_usable_directory_opens_as_a_workspace(
    start_drivnx: StartDrivnX, tmp_path: Path, state: str
) -> None:
    # CFX-APP-first-slice/AC-02
    workspace = tmp_path / "parent" / "workspace"
    if state != "new":
        workspace.mkdir(parents=True)
    if state in ("only a lock file", "crashed first launch"):
        (workspace / ".drivnx.lock").touch()
    if state == "crashed first launch":
        (workspace / "workspace.db").touch()

    start_drivnx("--no-browser", workspace=workspace).wait_for_launch()

    assert (workspace / "workspace.db").is_file()


def test_an_existing_workspace_opens_again(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-02
    workspace = tmp_path / "workspace"
    first = start_drivnx("--no-browser", workspace=workspace)
    first.wait_for_launch()
    first.process.terminate()
    first.process.wait(timeout=10)
    (workspace / "notes.txt").write_text("kept")

    start_drivnx("--no-browser", workspace=workspace).wait_for_launch()

    assert (workspace / "notes.txt").read_text() == "kept"


@posix_only
def test_a_new_workspace_is_owner_only_whatever_the_umask(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-17
    workspace = tmp_path / "workspace"

    start_drivnx("--no-browser", workspace=workspace, umask=0).wait_for_launch()

    assert mode(workspace) == 0o700
    assert mode(workspace / "workspace.db") == 0o600
    assert mode(workspace / ".drivnx.lock") == 0o600


@posix_only
def test_an_empty_directory_becomes_owner_only(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-17
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.chmod(0o755)

    start_drivnx("--no-browser", workspace=workspace).wait_for_launch()

    assert mode(workspace) == 0o700


def test_a_file_instead_of_a_directory_gets_a_clear_error(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-03
    file = tmp_path / "file.txt"
    file.write_text("x")

    drivnx = start_drivnx("--no-browser", workspace=file)

    assert drivnx.process.wait(timeout=10) == 1
    output = drivnx.all_output()
    assert "isn't a directory" in output
    assert "Traceback" not in output
    assert file.read_text() == "x"


def test_a_second_runtime_on_the_same_workspace_is_refused(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-04
    workspace = tmp_path / "workspace"
    first = start_drivnx("--no-browser", workspace=workspace)
    first.wait_for_launch()

    second = start_drivnx("--no-browser", workspace=workspace)

    assert second.process.wait(timeout=10) == 1
    output = second.all_output()
    assert "already open" in output
    assert "launch=" not in output
    assert first.process.poll() is None


@posix_only
def test_a_killed_runtime_does_not_block_the_next_one(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-04: the lock file stays on disk, and only the held lock counts.
    workspace = tmp_path / "workspace"
    first = start_drivnx("--no-browser", workspace=workspace)
    first.wait_for_launch()

    first.process.send_signal(signal.SIGKILL)
    first.process.wait(timeout=10)
    assert (workspace / ".drivnx.lock").exists()

    start_drivnx("--no-browser", workspace=workspace).wait_for_launch()


def test_two_different_workspaces_can_run_at_once(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-04
    start_drivnx("--no-browser", workspace=tmp_path / "one").wait_for_launch()
    start_drivnx("--no-browser", workspace=tmp_path / "two").wait_for_launch()


def test_no_argument_opens_the_default_workspace(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # ADR 0001. CFX-APP-first-slice/AC-01 is finished in T04.
    start_drivnx("--no-browser", workspace=None).wait_for_launch()

    assert (tmp_path / "home" / "DrivnX" / "default" / "workspace.db").is_file()


@pytest.mark.parametrize(
    "folder",
    [
        "Dropbox",
        "OneDrive",
        "OneDrive - Acme",
        "Google Drive",
        "iCloud Drive",
        "iCloudDrive",
    ],
)
def test_a_workspace_in_a_sync_folder_gets_a_warning(
    start_drivnx: StartDrivnX, tmp_path: Path, folder: str
) -> None:
    # CFX-APP-first-slice/AC-18
    drivnx = start_drivnx("--no-browser", workspace=tmp_path / folder / "workspace")
    drivnx.wait_for_launch()
    drivnx.process.terminate()
    drivnx.process.wait(timeout=10)

    output = drivnx.all_output()
    assert "sync folder" in output
    assert folder in output


def test_a_workspace_elsewhere_gets_no_warning(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-18: a folder name that merely contains the word isn't a sync folder.
    drivnx = start_drivnx(
        "--no-browser", workspace=tmp_path / "my-dropbox-notes" / "workspace"
    )
    drivnx.wait_for_launch()
    drivnx.process.terminate()
    drivnx.process.wait(timeout=10)

    assert "sync folder" not in drivnx.all_output()
