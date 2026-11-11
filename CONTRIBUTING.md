# Working on drivnx-web

How to work in this repo, and how to use the AI skills that come with it. The commands to run, test and build are in
the [README](README.md); this guide is about the workflow around them.

## 1. Set up

This repo sits in a `DrivnX/` workspace folder next to drivnx-docs, because its docs link to `../drivnx-docs/docs/` (the
Phase 1 direction) and its skill setup to `../drivnx-docs/ai-skills/`. The `drivnx` command from
[drivnx-cli](https://github.com/trisonlab/drivnx-cli) sets it all up: it clones this repo next to drivnx-docs, installs
the dependencies below and connects Claude Code to Plane and Pumble (its README is the team guide):

```bash
curl -fsSL https://raw.githubusercontent.com/trisonlab/drivnx-cli/main/get.sh | sh                # macOS / Linux
irm https://raw.githubusercontent.com/trisonlab/drivnx-cli/main/get.ps1 | iex                     # Windows PowerShell
```

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm (the version is pinned in
`apps/web/package.json`), python3 for the guard hooks, and Chromium for the Playwright tests. Then follow
[README → Checks](README.md#checks) to install from the lockfiles.

## 2. Where the work is defined

| What | Where |
|---|---|
| What DrivnX is and why | `../drivnx-docs/docs/DrivnX_Phase1_Development_Direction.md`: approved and canonical; it wins over everything below |
| Features (stories, acceptance criteria, revision, approval) | `.ai/knowledge/requirements/CFX-APP-*.md` |
| Tickets (criteria, `blocked_by`, `state: ready / blocked / done`) | `.ai/knowledge/work/DRIVN-*.md`, mirrored in [Plane](https://app.plane.so/drivnx/) project DRIVN |
| Decisions | `docs/adr/` (index in `.ai/knowledge/decisions/adr-index.md`) |
| Threats and mitigations | `docs/threat-model/threat-register.md` |

Build from an approved feature and a `ready` ticket only. If a ticket turns out to be wrong, change the feature first
(a new revision), then cut the tickets again; don't quietly build something different.

A ticket's ID is its Plane key, such as `DRIVN-23`; tickets cut before Plane keep their old ID in `legacy_id`
(`CFX-APP-csv-profile-T03`). Features and criteria keep their permanent IDs, such as `CFX-APP-csv-profile` (Plane
`DRIVN-9`) and `CFX-APP-csv-profile/AC-24`, which never change, even if the product is renamed.

**Plane.** The repo is the source of truth and Plane mirrors it. A new ticket starts in Plane, as a child of its
feature's item, so it gets its key; then its file is written as `DRIVN-N.md` with `id` and `external_key` set to that
key. When a ticket's `state` changes in the file, move the Plane item to match (`ready` → Todo, in progress → In
Progress, `done` → Done, `blocked` → Backlog). With the Plane MCP server connected, the AI does this for you.

## 3. One ticket, start to finish

| # | Step | Done when | AI skill |
|---|---|---|---|
| 1 | Pick | One `ready` ticket whose `blocked_by` tickets are done | `next-step` |
| 2 | Orient | You've read the ticket, its feature and the code it touches | `knowledge-first` |
| 3 | Clear | Every criterion is testable and nothing open blocks it | `requirements`, `acceptance-criteria` |
| 4 | Branch | You're on the ticket's own branch (below) | `work-branch` |
| 5 | Plan | For more than one file: a short plan of steps, files and checks, agreed | `planning` |
| 6 | Build | Each criterion has a failing test that names its ID, then the code, then green | `tdd` |
| 7 | Verify | Whole suite and scans run, with a table of every criterion and its evidence | `verification` |
| 8 | Review | Diff checked against the ticket; findings fixed or accepted | `code-review` (`security` for input, auth, secrets) |
| 9 | Ship | Commits, PR, ticket set to `state: done`, Plane item moved to Done | `delivery`, `finish-branch` |
| 10 | Learn | Knowledge updated; rework explained | `knowledge-update`, `retrospective` |

**Branches.** One branch per ticket, created before its first change, named `feat/<ticket-id-lowercase>-<short-title>`,
e.g. `feat/drivn-31-earlier-runs`. Branch from `main`, or from the branch of the ticket
yours depends on while that one is unmerged.

**Commits.** Title `<ticket-id>: <description>`, with `Spec:`, `Ticket:` and `Criteria:` lines at the end. Each
criterion is listed in full, never as a range. The format and how to trace a line back to its ticket are in
[README → Tracing work by ID](README.md#tracing-work-by-id).

**Pull requests.** Open them against `main` on `trisonlab/drivnx-web`. CI (`.github/workflows/ci.yml`) runs the tests,
the secret scan and both dependency audits, and must pass before merging.

## 4. Using the AI skills

The skills are committed in this repo, so you get them by cloning; there's nothing to install for them.

| Tool | Skills read from | How to call one |
|---|---|---|
| Claude Code | `.claude/skills/` (rules in `CLAUDE.md`, subagents in `.claude/agents/`) | `/tdd`, `/verification`, … or just describe the task |
| Codex and other AGENTS.md tools | `.agents/skills/` (rules in `AGENTS.md`) | `$tdd`, `$verification`, … |

### Start here

Not sure what to do next? Run **`/next-step`** (`$next-step` in Codex). It reads the tickets, the git state and the
conversation, tells you which step of the table above you're on, and gives you a prompt to paste for the next skill.

A good first prompt names the ticket and the limits:

> /tdd DRIVN-31: start with AC-35. Read the ticket and its feature first; don't commit.

### Skills by stage

| Stage | Skills |
|---|---|
| Orient | `next-step`, `knowledge-first`, `search-first`, `research` |
| Shape a feature | `brainstorm`, `requirements`, `acceptance-criteria`, `domain-modeling`, `architecture`, `api`, `simplicity-guard` |
| UI design | `frontend-design`, `web-design-guidelines` |
| Plan | `planning`, `subagent-development`, `work-branch` |
| Build | `tdd`, `implementation`, `testing`, `frontend-testing`, `debugging`, `refactoring`, `performance`, `security`, `accessibility`, `python`, `react`, `database-engineer`, `postgres`, `docker`, `ci-cd` |
| Check | `verification`, `code-review`, `architecture-diff` |
| Ship and learn | `delivery`, `finish-branch`, `resolving-merge-conflicts`, `documentation`, `knowledge-update`, `retrospective` |

Each skill's `SKILL.md` says when to use it and when not to. The full list with one-line descriptions is in
`CLAUDE.md` / `AGENTS.md`.

There are three agent roles in `.ai/agents/`: **explorer** (read-only search through the code), **reviewer** (an
independent review before delivery) and **knowledge-curator** (keeps `.ai/knowledge/` true after a change). Claude
Code runs them as subagents; in other tools, ask the AI to take on the role.

### What the AI is told to do

`CLAUDE.md` and `AGENTS.md` give every session the same rules. Hold the AI to them:

- **Evidence, not claims.** "Done", "fixed" or "passing" only with command output from this session. Anything not run
  is reported as NOT RUN.
- **Knowledge is an index.** It searches `.ai/knowledge/` first, but source code, tests and requirements win when
  they disagree. Guesses are marked INFERRED.
- **The least new code.** It checks whether the code is needed, already exists, or comes with the platform or an
  installed dependency before writing any.
- **The ticket's own branch.** It stops and asks if you're on the wrong branch.

### Habits that work

- One ticket per chat. Start a fresh chat when the topic changes or the chat gets long.
- Start each step with the skill's name, so the AI follows that skill's procedure instead of improvising.
- Name the IDs (`CFX-APP-…/AC-07`) in prompts, tests and commits; that's what makes coverage traceable.
- Keep the gates in order: criteria before code, a failing test before a fix, verification before "done", review
  before commit.

### Guard hooks

`.ai/hooks/` holds deterministic guards. In Claude Code they run on their own (`.claude/settings.json`): before shell
commands, after file edits, and when a session ends. They:

- block destructive commands (force-pushing a protected branch, `rm -rf` of root or home, `--no-verify`, …)
- block credentials, and files such as `.env` or private keys
- block changes that make checks pass by weakening them (lint rules switched off, CI steps allowed to fail, …)
- warn when a dependency changes without its lockfile
- run the project's own checks before delivery

To run them on every `git commit` too, install the git hook once per clone (`ai-skills sync` does the same if you
have the CLI):

```bash
cat > .git/hooks/pre-commit <<'EOF'
#!/bin/sh
root="$(git rev-parse --show-toplevel)"
[ -f "$root/.ai/hooks/run.py" ] || exit 0
command -v python3 >/dev/null 2>&1 || { echo "ai-skills hooks: python3 not found; guards skipped" >&2; exit 0; }
exec python3 "$root/.ai/hooks/run.py" pre-commit --format git
EOF
chmod +x .git/hooks/pre-commit
```

If a guard blocks you, fix the cause. Don't bypass it or weaken the check.

### The `ai-skills` CLI (optional)

Everything above works without it. The CLI comes from AI Skill OS, which isn't published yet: **ask Trison Labs if
you need it.** It adds:

| Command | What it does |
|---|---|
| `ai-skills next` | What to work on: ready or stale tickets, criteria with no ticket or no test, uncommitted changes |
| `ai-skills which "<task>"` | Which installed skill matches a task |
| `ai-skills knowledge search <terms>` | Search `.ai/knowledge/` |
| `ai-skills knowledge graph <name>` | What a component, endpoint, table or criterion connects to, with file:line evidence |
| `ai-skills knowledge check` | Find knowledge, tickets or requirements that are stale or invalid |
| `ai-skills sync` | Re-render `CLAUDE.md` / `AGENTS.md` and reinstall the git hook |

Without the CLI, the skills read the ticket and requirement files directly.

### When the knowledge index conflicts

`.ai/knowledge/index/facts.yaml` and `.ai/knowledge/index/graph.yaml` are generated from the repo's files by
`ai-skills knowledge update`. Two branches that both change them conflict there, even when their code doesn't. Never
resolve those files by hand: rebuild them.

1. Bring your branch up to date by merging `main` into it (`git merge origin/main`). A merge needs only a normal
   push; a rebase of a pushed branch needs a force-push.
2. For the two index files, take `main`'s version:
   `git checkout --theirs .ai/knowledge/index/facts.yaml .ai/knowledge/index/graph.yaml`.
3. **With the CLI:** run `ai-skills knowledge update`, then `ai-skills knowledge check`. Run them in a clone that sits
   next to `drivnx-docs` (the usual `DrivnX/` layout): the index points at `../drivnx-docs/docs/`, and from anywhere
   else the requirement facts come out STALE or with absolute paths. The rebuilt `facts.yaml` should differ from
   `main`'s by little more than its `updated_at` line; if it doesn't, check the layout before committing.
4. **Without the CLI:** keep `main`'s version from step 2 and say in the PR that the index needs a rebuild. CI doesn't
   check the index, so nothing fails; someone with the CLI rebuilds it after the merge.
5. `git add` the two files, finish the merge and push.

### Changing the skill set

- Don't edit files in `.claude/skills/` or `.agents/skills/`: they're pinned in `.ai/lock.yaml`, and updates
  overwrite local edits.
- Don't edit inside the `ai-skills:begin … ai-skills:end` block of `CLAUDE.md` / `AGENTS.md`, for the same reason.
  Put notes for this repo outside the block.
- To add a skill to every DrivnX repo, add it to `../drivnx-docs/ai-skills/baseline.txt` and run `../drivnx-docs/ai-skills/drivnx-skills.sh
  sync`. To add one to this repo only, run `ai-skills add <skill>`. Both need the CLI.
