# conflux-web

The Conflux Phase 1 product: a local Python runtime with a browser UI that connects unfamiliar data and turns it into
evidence-backed, human-reviewed understanding (Data Profile → Data Dictionary → Data Contract).

- **Source of truth:** [../docs/Conflux_Phase1_Development_Direction.md](../docs/Conflux_Phase1_Development_Direction.md)
  (approved); open findings in [../docs/Conflux_Phase1_Review_Notes.md](../docs/Conflux_Phase1_Review_Notes.md)
- **Status:** first vertical slice in progress (`.ai/knowledge/requirements/CFX-APP-first-slice.md`): the runtime, its session protection,
  workspace setup, the app shell, connecting a CSV, paging through its rows, the trust-state core, profile run history rules and contract rules exist.
- **Stack (§4):** Python 3.12, FastAPI, Pydantic v2, DuckDB, SQLite · React + TypeScript + Vite, TanStack, Radix/shadcn ·
  uv and pnpm with committed lockfiles · pytest, Vitest, Playwright, Docker Compose PostgreSQL fixture

## Layout (§10)

```text
apps/local-api      FastAPI runtime: sessions, jobs, files, credentials, DuckDB, SQLite, AI providers; serves the UI
apps/web            React + TypeScript + Vite browser UI
packages/           domain, connectors, profiling, artifacts, ai, contracts
tests/              golden, integration, e2e
testdata/           synthetic datasets only
docs/               adr, threat-model, product-research
.ai/                AI Skill OS: manifest, lock, knowledge
```

## Development

Run the UI with hot reload, in two terminals:

```bash
pnpm --dir apps/web dev                                             # Vite on 127.0.0.1:5173; proxies /api to port 8787 (API_PORT)
uv run conflux --port 8787 --no-browser --dev-origin http://127.0.0.1:5173   # prints a link to open in the browser
```

## Checks

CI runs these on every pull request and on `main` (`.github/workflows/ci.yml`). Install first, from the lockfiles:

```bash
uv sync --frozen
pnpm --dir apps/web install --frozen-lockfile
uv run --frozen playwright install --with-deps chromium    # only if there's no system Chromium
```

pytest, then Playwright, then Vitest. The first two each build and install the wheel:

```bash
uv run --frozen pytest tests/integration tests/golden
uv run --frozen pytest tests/e2e
pnpm --dir apps/web test
```

The scans below are for Linux and macOS, as they use `xargs` and `/dev/stdin`. CI runs them on Linux. In a shell, run
`set -o pipefail` first, so a failure early in a pipe can't look like a clean result.

Secret scanning looks at the current files, not at git history, and finds what `detect-secrets` recognises, which isn't
every credential. A fake credential a test uses on purpose carries `# pragma: allowlist secret`:

```bash
git ls-files -z | xargs -0 uvx --from detect-secrets@1.5.0 detect-secrets-hook --exclude-files '\.ai/hooks/secret-scan/hook\.py|\.ai/lock\.yaml|pnpm-lock\.yaml|uv\.lock|\.ai/knowledge/index/'
```

Dependency vulnerabilities, for Python and then the UI:

```bash
uv export --frozen --no-emit-project --no-hashes --no-header --no-annotate | uvx pip-audit==2.10.1 --disable-pip --no-deps -r /dev/stdin
pnpm --dir apps/web audit
```

## Building a release

This builds the UI from this checkout and pins what running Conflux needs to `uv.lock`. It fails if the UI doesn't build
or a lockfile has fallen behind. Install from the wheel: installing from this folder ignores the pins, as `uv` then reads
the ranges in `pyproject.toml`:

```bash
uv build --wheel
uv tool install dist/conflux_data-*.whl
```

## Tracing work by ID

Specs, tickets and criteria have permanent IDs, such as `CFX-APP-first-slice`, `CFX-APP-first-slice-T01` and
`CFX-APP-first-slice/AC-07`. **An ID never changes, even if the product is renamed.** Tests name the criteria they prove.

A ticket commit's title is `<ticket-id>: <description>`. Every commit that belongs to a spec ends with its IDs, listing
each criterion in full and never as a range:

```text
CFX-APP-first-slice-T03: Open workspaces safely

Spec: CFX-APP-first-slice
Ticket: CFX-APP-first-slice-T03
Criteria: AC-02, AC-03, AC-04
```

To trace: `git log --grep="CFX-APP-first-slice-T03"` finds a ticket's commits, `git blame` leads from a line to its commit
and ticket, and `ai-skills knowledge graph CFX-APP-first-slice/AC-07` shows a criterion's ticket and tests.

## Branches

Each ticket gets its own branch with the ticket ID in its name, created before its first change. While the ticket it
depends on is unmerged, branch from that ticket's branch; otherwise branch from `main`. `/tdd` and `/delivery` stop and
ask if the current branch isn't the ticket's.

## AI skills

Set up with `../ai-skills/conflux-skills.sh` (the shared Conflux baseline plus this repo's stack skills); the list is in
`.ai/manifest.yaml`.
