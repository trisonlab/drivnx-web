---
name: delivery
description: Ships a verified change - focused commits with clear messages, a pull request description, release notes and changelog entries, version bumps, and a deploy and rollback checklist - without pushing, merging or deploying unless explicitly asked. Use when a change is done and needs to be committed, turned into a pull request, released or deployed.
license: MIT
---

# Delivery

## Purpose
Turn finished, verified work into something reviewable and releasable, and make it safe to undo.

## When to use
- "Commit this", "open a PR", "write the PR description", "prepare a release", "write release notes", "deploy this".

## When NOT to use
- The work is not verified yet: run `verification` first. Delivery never skips it.
- Handing a whole project over to a client: use `handoff` (freelance).
- Reviewing someone else's change: use `code-review`.

## Procedure
1. **Confirm it is verified**: fresh test/lint/build results from `verification`. If missing or failing, stop and say so.
2. **Check the branch**: when the change belongs to a ticket, the current branch must be that ticket's own (its name contains the ticket ID). If it is not, stop and ask the user before committing, and offer `work-branch`. That earlier tickets went on this branch is not a reason to add another.
3. **Shape the commits**: one logical change per commit; no unrelated files, debug code, secrets or generated junk (check `git status` and the diff). Message: imperative subject under ~70 characters, body saying what changed and why.
4. **Pull request description**: what and why, how it was tested (actual commands and results), risks, migrations or config changes, screenshots for UI, follow-ups. Link the requirement or ticket. Lead with the smallest picture that makes the change clear (a call tree, file tree, pseudocode or a small diff of the shape, not prose); show evidence as before/after (failing then passing test, old then new screenshot or output); and state the merge danger: a two-way door (cheap to roll back) or a one-way door (destructive or hard to reverse), and the blast radius.
5. **Release** (when releasing): bump the version by the project's scheme (semver: breaking = major, feature = minor, fix = patch), add a changelog entry in the project's existing format, write release notes for the audience (users vs developers).
6. **Deploy checklist** (when deploying): pre-checks (CI green on this commit, migrations reviewed, config/secrets present in the target environment), the deploy steps the project documents, post-deploy smoke checks, and a **rollback plan** including what happens to data and migrations.
7. **Act only as asked**: prepare commits and texts by default. Push, merge, tag, publish or deploy only on an explicit request, and never force-push shared branches.

## Output
Commit list (subject + files), PR description, and when relevant release notes and a deploy/rollback checklist.

## Failure modes
- Delivering unverified work, or describing tests that were not run.
- One huge commit mixing refactors, features and formatting.
- Committing a ticket on another ticket's branch, so one branch collects several tickets.
- PR descriptions that restate the diff instead of explaining why and how it was tested.
- Deploying with no rollback plan, or irreversible migrations shipped with the code that needs them.
- Pushing or deploying without being asked.
