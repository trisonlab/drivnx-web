---
name: work-branch
description: Starts and keeps a clean git working branch for one piece of work - check the current state, branch from an up-to-date base with a descriptive name, make small focused commits with clear messages, keep unrelated changes and secrets out, and stay current with the base branch. Use when starting a task, ticket or fix in a git repository, when asked to create a branch, or when committing work in progress.
license: MIT
---

# Work branch

## Purpose
Every piece of work lives on its own branch with a readable history, so it can be reviewed, reverted or abandoned safely.

## When to use
- Starting a ticket, feature or fix; "create a branch", "commit this"; keeping a branch up to date while working.

## When NOT to use
- Working on two branches at once in separate folders: `worktrees`.
- Wrapping up (final verification, merge or PR, cleanup): `finish-branch`. PR descriptions, releases, deploys: `delivery`.

## Procedure
1. **Check the state first**: `git status`, current branch, uncommitted changes. Never carry someone's uncommitted work into a new branch silently: commit, stash or ask.
2. **Branch from an up-to-date base**: the project's main branch (or the one the user names), after `git fetch` when a remote exists. Name it by the project's convention, else `<type>/<ticket>-<short-desc>` (e.g. `fix/shop-42-coupon-limit`). A ticket gets its own branch with its ID in the name, created before its first change; never reuse another ticket's branch. If it depends on a ticket that is not merged yet, branch from that ticket's branch.
3. **Commit in small logical steps**: one reason per commit; review `git diff --staged` before committing; imperative subject under ~70 characters, body saying why when it is not obvious. Follow the project's convention (e.g. Conventional Commits) if it has one.
4. **Keep commits clean**: no debug code, generated files, `.env`, keys or unrelated formatting. The repository's pre-commit hooks are guards, not obstacles: never bypass them with `--no-verify`.
5. **Stay current**: integrate the base branch regularly the way the project does (rebase for a private branch, merge for a shared one). Never rewrite history others have pulled.
6. **Pushing is the user's call**: push only when asked, never force-push a shared branch.

## Output
The branch name, the commits made (subjects), and the current state relative to the base branch.

## Failure modes
- Committing directly on main; branching from a stale base.
- Letting one branch collect several tickets because the next ticket started without a new branch.
- One giant commit, or commits mixing unrelated changes.
- `--no-verify`, force-pushing shared branches, committing secrets.
