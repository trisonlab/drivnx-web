import os
import re
import shutil
import socket
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
URL = re.compile(r"http://127\.0\.0\.1:(\d+)/\S*")
START_TIMEOUT_SECONDS = 30


def run(*command: str | Path, cwd: Path = REPO_ROOT) -> None:
    arguments = [str(part) for part in command]
    result = subprocess.run(
        arguments, cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.fail(
            f"`{' '.join(arguments)}` exited with {result.returncode}:\n"
            f"{result.stdout}{result.stderr}"
        )


@pytest.fixture(scope="session")
def conflux_executable(tmp_path_factory: pytest.TempPathFactory) -> str:
    """The `conflux` command from a wheel built and installed the way a user gets it."""
    work = tmp_path_factory.mktemp("installed")
    run("pnpm", "--dir", "apps/web", "build")
    run("uv", "build", "--wheel", "--out-dir", work / "wheel")
    run("uv", "venv", "--python", "3.12", work / "venv")
    python = work / "venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    wheel = next((work / "wheel").glob("*.whl"))
    run("uv", "pip", "install", "--python", python, wheel)
    executable = shutil.which("conflux", path=str(python.parent))
    assert executable, "the wheel did not install a conflux command"
    return executable


@pytest.fixture
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def unused_port(free_port: int) -> int:
    """A second free port, for tests that need two."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert port != free_port
    return port


@dataclass
class Launch:
    """The parts of a printed launch URL."""

    url: str
    origin: str
    port: int
    code: str


@dataclass
class RunningConflux:
    process: subprocess.Popen[str]
    output: "Queue[str]"
    read_so_far: list[str] = field(default_factory=list)

    def wait_for_url(self) -> re.Match[str]:
        """Return the launch URL once the process prints it."""
        lines = self.read_so_far
        while True:
            try:
                line = self.output.get(timeout=START_TIMEOUT_SECONDS)
            except Empty:
                pytest.fail(
                    f"no launch URL within {START_TIMEOUT_SECONDS}s; output: {lines}"
                )
            if line == "":
                pytest.fail(
                    f"conflux exited with {self.process.wait()} and no URL; output: {lines}"
                )
            lines.append(line)
            if match := URL.search(line):
                return match

    def wait_for_launch(self) -> Launch:
        match = self.wait_for_url()
        url = match.group(0)
        code = url.partition("#launch=")[2]
        return Launch(
            url, f"http://127.0.0.1:{match.group(1)}", int(match.group(1)), code
        )

    def all_output(self) -> str:
        """Everything the process printed, for a process that has exited."""
        while True:
            line = self.output.get(timeout=5)
            if line == "":
                return "".join(self.read_so_far)
            self.read_so_far.append(line)


StartConflux = Callable[..., "RunningConflux"]
_NEW_WORKSPACE = object()


@pytest.fixture
def start_conflux(conflux_executable: str, tmp_path: Path) -> Iterator[StartConflux]:
    """Start `conflux` with the given arguments, and stop it when the test ends."""
    running: list[subprocess.Popen[str]] = []

    def start(
        *args: str,
        browser: str | None = None,
        env: dict[str, str] | None = None,
        umask: int = -1,
        workspace: Path | None | object = _NEW_WORKSPACE,
    ) -> RunningConflux:
        # Without these, Python's webbrowser module would try the real browsers on this desktop.
        hidden = {"BROWSER", "DISPLAY", "WAYLAND_DISPLAY", "TERM"}
        process_env = {
            name: value for name, value in os.environ.items() if name not in hidden
        }
        # Conflux creates its default workspace under the home directory: never the real one here.
        process_env["HOME"] = str(tmp_path / "home")
        process_env.update(env or {})
        if browser is not None:
            process_env["BROWSER"] = browser
        # By default each process gets its own workspace, as two can't share one (ADR 0001).
        if workspace is _NEW_WORKSPACE:
            arguments = [*args, str(tmp_path / f"workspace-{len(running)}")]
        elif workspace is None:
            arguments = list(args)
        else:
            arguments = [*args, str(workspace)]
        process = subprocess.Popen(
            [conflux_executable, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=process_env,
            umask=umask,
        )
        running.append(process)
        output: Queue[str] = Queue()

        def pump() -> None:
            assert process.stdout
            for line in process.stdout:
                output.put(line)
            output.put("")

        Thread(target=pump, daemon=True).start()
        return RunningConflux(process, output)

    yield start
    for process in running:
        process.terminate()
        process.wait(timeout=10)
