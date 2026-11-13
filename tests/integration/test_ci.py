"""CI runs the project's real checks, and each one runs locally the same way, CFX-APP-first-slice AC-53."""

import ast
import re

import pytest

from ..conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
README = REPO_ROOT / "README.md"
POSTGRES_FIXTURE = "uv run --frozen python tests/postgres_fixture.py start"
CHECKS = {
    "PostgreSQL fixture": POSTGRES_FIXTURE,
    "pytest": "uv run --frozen pytest tests/integration tests/golden",
    "Playwright": "uv run --frozen pytest tests/e2e",
    "Vitest": "pnpm --dir apps/web test",
    "secret scanning": (
        "git ls-files -z | xargs -0 uvx --from detect-secrets@1.5.0 detect-secrets-hook "
        "--exclude-files '\\.ai/hooks/secret-scan/hook\\.py|\\.ai/lock\\.yaml|pnpm-lock\\.yaml|uv\\.lock|\\.ai/knowledge/index/'"
    ),
    "Python dependency vulnerabilities": (
        "uv export --frozen --no-emit-project --no-hashes --no-header --no-annotate "
        "| uvx pip-audit==2.10.1 --disable-pip --no-deps -r /dev/stdin"
    ),
    "UI dependency vulnerabilities": "pnpm --dir apps/web audit",
}


@pytest.mark.parametrize("command", CHECKS.values(), ids=CHECKS)
def test_each_check_runs_in_ci_and_locally_with_one_command(command: str) -> None:
    # CFX-APP-first-slice/AC-53: the same words in both, so they can't drift apart.
    assert f"run: {command}\n" in WORKFLOW.read_text(encoding="utf-8")
    assert f"\n{command}\n" in README.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "command", [command for command in CHECKS.values() if " | " in command]
)
def test_a_failure_early_in_a_pipe_fails_the_step(command: str) -> None:
    # GitHub's default shell has no pipefail, and a failed export would read as a clean audit.
    # Naming bash turns pipefail on.
    assert f"run: {command}\n        shell: bash\n" in WORKFLOW.read_text(encoding="utf-8")


def test_no_step_can_fail_without_failing_the_run() -> None:
    # CFX-APP-first-slice/AC-53: these would keep the commands above in the file and still skip them.
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "continue-on-error" not in workflow
    assert "if:" not in workflow


APT_RETRIES = (
    "[ \"$RUNNER_OS\" != Linux ] || echo 'Acquire::Retries \"5\";' | sudo tee /etc/apt/apt.conf.d/80-retries"
)


def test_only_the_apt_retries_depend_on_the_platform() -> None:
    # CFX-APP-first-slice/AC-53: a platform test in a step's shell could skip a check the way `if:` would. The one
    # allowed sets up apt on Linux and runs no check.
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "runner.os" not in workflow
    assert workflow.count("RUNNER_OS") == 1
    assert f"run: |\n          {APT_RETRIES}\n        shell: bash\n" in workflow


def test_the_postgres_fixture_starts_before_the_tests_and_ci_requires_it() -> None:
    # CFX-APP-postgres/AC-34: without a server the PostgreSQL tests would skip, and CI must fail
    # instead. Its platform setup lives in that one script, so the rules above still hold.
    workflow = WORKFLOW.read_text(encoding="utf-8")
    tests = workflow[workflow.index("\n  tests:\n") : workflow.index("\n  scans:\n")]

    assert '\n    env:\n      DRIVNX_REQUIRE_POSTGRES: "1"\n' in tests
    assert f"run: {POSTGRES_FIXTURE}\n" in tests
    assert tests.index(POSTGRES_FIXTURE) < tests.index(CHECKS["pytest"])


def test_ci_runs_on_every_pull_request_and_on_main() -> None:
    assert (
        "\non:\n  pull_request:\n  push:\n    branches: [main]\n"
        in WORKFLOW.read_text(encoding="utf-8")
    )


def test_a_hung_job_is_stopped() -> None:
    # Untried platforms could hang, and GitHub's own limit is 6 hours.
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("timeout-minutes: 30\n") == workflow.count("runs-on:") == 2


def test_no_test_file_needs_a_posix_module_just_to_load() -> None:
    # The T02 review: on Windows this stops the whole run while collecting, whatever a test's marks.
    posix_modules = {"fcntl", "grp", "pty", "pwd", "resource", "termios"}
    found = []
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            found += [f"{path.name}: {name}" for name in names if name in posix_modules]

    assert found == []


def test_ci_installs_from_the_lockfiles() -> None:
    # CFX-APP-first-slice/AC-50 and CFX-APP-first-slice/AC-53.
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "run: uv sync --frozen\n" in workflow
    assert "run: pnpm --dir apps/web install --frozen-lockfile\n" in workflow


def test_ci_runs_on_every_target_platform() -> None:
    # The spec's assumptions: Linux, macOS and Windows are targets, and CI is how the last two run.
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow


def test_every_action_is_pinned_to_a_commit() -> None:
    # Section 7 of the direction doc: a tag can be moved to other code, and a commit can't.
    used = re.findall(r"uses: (\S+)", WORKFLOW.read_text(encoding="utf-8"))

    assert used
    assert [
        action for action in used if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", action)
    ] == []


def test_ci_can_only_read_the_repository() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "\npermissions:\n  contents: read\n" in workflow
    assert workflow.count("permissions:") == 1
