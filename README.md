# conflux-web

The Conflux Phase 1 product: a local Python runtime with a browser UI that connects unfamiliar data and turns it into
evidence-backed, human-reviewed understanding (Data Profile → Data Dictionary → Data Contract).

- **Source of truth:** [../docs/Conflux_Phase1_Development_Direction.md](../docs/Conflux_Phase1_Development_Direction.md)
  (approved); open findings in [../docs/Conflux_Phase1_Review_Notes.md](../docs/Conflux_Phase1_Review_Notes.md)
- **Status:** first vertical slice in progress (`.ai/knowledge/requirements/CFX-APP-first-slice.md`): the runtime, its session
  protection and a placeholder page exist.
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

Run the checks with `uv run --frozen pytest` and `pnpm --dir apps/web test`.

## AI skills

Set up with `../ai-skills/conflux-skills.sh` (the shared Conflux baseline plus this repo's stack skills); the list is in
`.ai/manifest.yaml`.
