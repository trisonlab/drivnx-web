import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[1]
URL = re.compile(r"http://127\.0\.0\.1:(\d+)/\S*")
START_TIMEOUT_SECONDS = 30
CREDENTIAL_SHAPES = {
    "a password in a URL": rb"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:[^\s/@]+@",
    "a named secret": (
        rb"(?i)(?:password|passwd|pwd|secret|api[_-]?key|token)[a-z0-9_-]*\\?[\"']?\s*[=:]\s*"
        rb"\\?[\"']?[^\s\"',}\\]{6,}"
    ),
    "a basic auth header": (
        rb"(?i)authorization\\?[\"']?\s*[=:]\s*\\?[\"']?basic\s+[a-z0-9+/=]{8,}"
    ),
    "a bearer token": rb"(?i)bearer\s+[a-z0-9._~+/-]{16,}",
    "an AWS access key": rb"\bAKIA[0-9A-Z]{16}\b",
    "an API key": rb"\bsk-[A-Za-z0-9_-]{20,}\b",
    "a GitHub token": rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    "a private key": rb"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}


# What the spec's assumptions call NOT RUN until CI can run it, so these are unproven (T02 review).
posix_only = pytest.mark.skipif(
    os.name == "nt", reason="uses POSIX signals, modes, links or shell scripts"
)
linux_only = pytest.mark.skipif(
    sys.platform != "linux",
    reason="the DISPLAY rules are Linux's, and elsewhere this could open a real browser",
)


def credential_shaped_values(data: bytes) -> list[str]:
    """What kinds of credential this holds, by name, so a failure doesn't print the value."""
    return [name for name, shape in CREDENTIAL_SHAPES.items() if re.search(shape, data)]


class RisingClock:
    """Each reading is later than the last, unless stopped. A real clock can repeat itself."""

    def __init__(self) -> None:
        self.moment = datetime(2026, 1, 1, tzinfo=UTC)
        self.stopped = False

    def __call__(self) -> str:
        if not self.stopped:
            self.moment += timedelta(seconds=1)
        return self.moment.isoformat()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> RisingClock:
    rising = RisingClock()
    monkeypatch.setattr("artifacts.provenance.now", rising)
    monkeypatch.setattr("artifacts.profiles.now", rising)
    return rising


WRITTEN_ONCE = {
    "contract_revision",
    "contract_rule",
    "contract_rule_field",
    "contract_rule_revision",
    "dictionary_entry",
    "dictionary_entry_revision",
    "finding",
    "finding_observation",
    "observation",
    "provenance_event",
}
WATCHED = {*WRITTEN_ONCE, "profile_run"}
RUN_COLUMNS_THAT_CHANGE = {"status", "ended_at"}
# SQLite's authorizer reports these as inserts, and it can't see what they replace. SQL has too many
# ways to write them to read them exactly, so the word and a watched table anywhere after it count.
REPLACING = re.compile(rf"(?is)\bREPLACE\b.*?\b({'|'.join(sorted(WATCHED))})\b")


@pytest.fixture(scope="session", autouse=True)
def history_changes() -> Iterator[list[str]]:
    # CFX-APP-first-slice/AC-42 (ADR 0002 point 4), for what this process runs through a SQLAlchemy
    # engine. SQLite says what each update or delete touches, also from a trigger or through the
    # engine's driver connection. Not watched: a REPLACE in a trigger or through that driver
    # connection, `ALTER TABLE`, and a separate `sqlite3.connect`, such as a separately started
    # `conflux`. Production has no route writing these tables yet.
    found: list[str] = []

    def authorize(action, table, column, _database, _trigger) -> int:
        if action == sqlite3.SQLITE_UPDATE and (
            table in WRITTEN_ONCE
            or (table == "profile_run" and column not in RUN_COLUMNS_THAT_CHANGE)
        ):
            found.append(f"update of {table}.{column}")
        elif action == sqlite3.SQLITE_DELETE and table in WATCHED:
            found.append(f"delete from {table}")
        return sqlite3.SQLITE_OK

    def watch_connection(dbapi_connection, _record) -> None:
        dbapi_connection.set_authorizer(authorize)

    def read_statement(
        _connection, _cursor, statement, _parameters, _context, _many
    ) -> None:
        replacing = REPLACING.search(statement)
        if replacing:
            found.append(f"replace in {replacing.group(1)}")

    event.listen(Engine, "connect", watch_connection)
    event.listen(Engine, "before_cursor_execute", read_statement)
    yield found
    event.remove(Engine, "before_cursor_execute", read_statement)
    event.remove(Engine, "connect", watch_connection)
    assert not found, f"these changed written history: {found}"


@pytest.fixture(scope="session", autouse=True)
def no_workspace_holds_a_credential(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    # CFX-APP-first-slice/AC-48: every workspace this run created, backups included. Every test's
    # temporary directory sits under this one.
    yield
    found = {
        str(path): shapes
        for path in tmp_path_factory.getbasetemp().rglob("workspace.db*")
        if path.is_file() and (shapes := credential_shaped_values(path.read_bytes()))
    }
    assert not found, f"credential-shaped values in workspace files: {found}"


def run(
    *command: str | Path, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None
) -> None:
    arguments = [str(part) for part in command]
    result = subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
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
    run("uv", "build", "--wheel", "--out-dir", work / "wheel")
    wheel = next((work / "wheel").glob("*.whl"))
    # Private folders, so this neither sees nor changes what the user has installed.
    commands = work / "bin"
    run(
        "uv",
        "tool",
        "install",
        "--python",
        "3.12",
        wheel,
        env={"UV_TOOL_DIR": str(work / "tools"), "UV_TOOL_BIN_DIR": str(commands)},
    )
    executable = shutil.which("conflux", path=str(commands))
    assert executable, "the wheel did not install a conflux command"
    return executable


@pytest.fixture(scope="session")
def installed_environment(conflux_executable: str) -> Path:
    """The Python environment holding the installed `conflux`."""
    return Path(conflux_executable).parents[1] / "tools" / "conflux-data"


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
        cwd: Path | None = None,
    ) -> RunningConflux:
        # Without these, Python's webbrowser module would try the real browsers on this desktop.
        hidden = {"BROWSER", "DISPLAY", "WAYLAND_DISPLAY", "TERM"}
        process_env = {
            name: value for name, value in os.environ.items() if name not in hidden
        }
        # Conflux creates its default workspace under the home directory: never the real one here.
        process_env["HOME"] = process_env["USERPROFILE"] = str(tmp_path / "home")
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
            cwd=cwd,
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
