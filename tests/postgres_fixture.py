"""The PostgreSQL fixture that tests run against (CFX-APP-postgres AC-34, ADR 0006).

`uv run --frozen python tests/postgres_fixture.py start` before the tests, locally and in CI, and
`stop` afterwards. Where Docker runs Linux containers, it's the Compose service in
`testdata/postgres/`. Elsewhere, as on GitHub's macOS and Windows runners, it starts a server of
its own in the temporary folder: from `PGBIN` (set on GitHub's Windows runners), else Homebrew's
postgresql@17, installed if missing, else a `pg_ctl` on the path. Both run the same `fixture.sql`.
This is the one place the setup depends on the platform, as CI's steps can't (`test_ci.py`).
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

HOST = "127.0.0.1"
PORT = 54329
START = "uv run --frozen python tests/postgres_fixture.py start"
USER = "drivnx_fixture"
PASSWORD = "drivnx-fixture"  # pragma: allowlist secret, ai-skills: allow
DATABASE = "drivnx_fixture"

FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "postgres"
COMPOSE = ["docker", "compose", "--file", str(FIXTURE / "compose.yaml")]
OWN_SERVER = Path(tempfile.gettempdir()) / "drivnx-postgres-fixture"
SUPERUSER_PASSWORD = "fixture-superuser"  # pragma: allowlist secret, ai-skills: allow


def dsn() -> str:
    """How DuckDB reaches the fixture as the read-only role `fixture.sql` creates."""
    return f"host={HOST} port={PORT} dbname={DATABASE} user={USER} password={PASSWORD}"


def _compose_runs_linux() -> bool:
    """Whether Docker here has Compose and runs Linux containers, as GitHub's Windows runner
    doesn't, and as a Docker without the Compose plugin can't use."""
    if shutil.which("docker") is None:
        return False
    if subprocess.run([*COMPOSE[:2], "version"], capture_output=True).returncode:
        return False
    found = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True
    )
    return found.returncode == 0 and found.stdout.strip() == "linux"


def _binaries() -> Path:
    """Where `initdb`, `pg_ctl` and `psql` are, installing Homebrew's if that's the only way."""
    if os.environ.get("PGBIN"):
        return Path(os.environ["PGBIN"])
    if shutil.which("brew"):
        if subprocess.run(["brew", "list", "postgresql@17"], capture_output=True).returncode:
            # Without updating Homebrew itself first, which is slow and sometimes fails on CI.
            subprocess.run(
                ["brew", "install", "postgresql@17"],
                env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"},
                check=True,
            )
        prefix = subprocess.run(
            ["brew", "--prefix", "postgresql@17"], capture_output=True, text=True, check=True
        )
        return Path(prefix.stdout.strip()) / "bin"
    pg_ctl = shutil.which("pg_ctl")
    if pg_ctl is None:
        sys.exit(
            "Nothing here can start the PostgreSQL fixture. It needs Docker with Compose running"
            " Linux containers, PGBIN set to a PostgreSQL bin folder, Homebrew, or pg_ctl on the"
            " path."
        )
    return Path(pg_ctl).parent


def _listening() -> bool:
    try:
        socket.create_connection((HOST, PORT), timeout=2).close()
    except OSError:
        return False
    return True


def _start_own_server() -> None:
    """Start a server of its own, and leave nothing behind if any part fails, as the next start
    would take a folder for a running server."""
    binaries = _binaries()
    try:
        _initialise_and_start(binaries)
    except BaseException:
        # A server that started before a later part failed would hold the folder open.
        if (OWN_SERVER / "data" / "postmaster.pid").exists():
            subprocess.run(
                [str(binaries / "pg_ctl"), "--pgdata", str(OWN_SERVER / "data"), "stop"],
                capture_output=True,
            )
        shutil.rmtree(OWN_SERVER, ignore_errors=True)
        raise


def _initialise_and_start(binaries: Path) -> None:
    data = OWN_SERVER / "data"
    password_file = OWN_SERVER / "superuser-password"
    OWN_SERVER.mkdir(parents=True)
    password_file.write_text(SUPERUSER_PASSWORD, encoding="utf-8")
    subprocess.run(
        [
            str(binaries / "initdb"),
            "--pgdata", str(data),
            "--username", "postgres",
            "--pwfile", str(password_file),
            "--auth-host", "scram-sha-256",
            "--auth-local", "scram-sha-256",
            "--encoding", "UTF8",
            "--locale", "C",
        ],
        check=True,
    )
    # In the configuration, not on pg_ctl's command line, which shells quote differently. TCP only:
    # a build's default socket folder can be one this user can't write, as on Debian.
    with (data / "postgresql.conf").open("a", encoding="utf-8") as settings:
        settings.write(
            f"port = {PORT}\nlisten_addresses = '{HOST}'\nunix_socket_directories = ''\n"
        )
    subprocess.run(
        [
            str(binaries / "pg_ctl"),
            "--pgdata", str(data),
            "--log", str(OWN_SERVER / "server.log"),
            "--wait",
            "start",
        ],
        check=True,
    )
    subprocess.run(
        [
            str(binaries / "psql"),
            "--host", HOST,
            "--port", str(PORT),
            "--username", "postgres",
            "--dbname", "postgres",
            "--set", "ON_ERROR_STOP=1",
            "--file", str(FIXTURE / "fixture.sql"),
        ],
        env={**os.environ, "PGPASSWORD": SUPERUSER_PASSWORD},
        check=True,
    )


def start() -> None:
    if _compose_runs_linux():
        subprocess.run([*COMPOSE, "up", "--detach", "--wait"], check=True)
    elif not _listening():
        # A folder with nothing listening is what a start that died left behind.
        shutil.rmtree(OWN_SERVER, ignore_errors=True)
        _start_own_server()
    print(f"The PostgreSQL fixture is running on {HOST}:{PORT}.")


def stop() -> None:
    if OWN_SERVER.exists():
        subprocess.run(
            [str(_binaries() / "pg_ctl"), "--pgdata", str(OWN_SERVER / "data"), "stop"],
            check=False,
        )
        shutil.rmtree(OWN_SERVER)
    elif _compose_runs_linux():
        # Volumes too, so the next start runs fixture.sql on an empty database again.
        subprocess.run([*COMPOSE, "down", "--volumes"], check=True)


if __name__ == "__main__":
    {"start": start, "stop": stop}[sys.argv[1]]()
