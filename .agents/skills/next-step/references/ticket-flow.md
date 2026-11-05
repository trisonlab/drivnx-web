# Implementing a ticket: the flow and its gates

Use it top to bottom. The **first gate that is not met** is where the user is; its skill is the next step.

| # | Stage | Gate (evidence it is met) | If not met → skill |
|---|---|---|---|
| 0 | Blockers | No merge/rebase in progress, build green on the base branch, `ai-skills knowledge check` clean | `resolving-merge-conflicts` / `debugging` / the project's to-spec skill |
| 1 | Pick | One ticket chosen, `state: ready`, blockers done, its `feature_revision` equals the feature's current revision | `ai-skills next` to pick; a stale ticket → the project's to-ticket skill |
| 2 | Orient | Ticket, feature item and the code it touches have been read (`ai-skills context "<ticket title>"`, `ai-skills knowledge graph <ticket-id>`) | `knowledge-first` |
| 3 | Clear | Every criterion in the ticket is testable and agreed; no open question blocks it | the project's to-spec skill (never guess a rule) |
| 4 | Branch | The current branch or worktree is this ticket's own, with its ID in the name; another ticket's branch does not meet this gate, so recommend the skill rather than offering it as an aside (skip only when the user has said not to use branches) | `work-branch` / `worktrees` |
| 5 | Plan | For more than one file or one seam: a short plan of steps, files and checks, agreed | `planning` (`simplicity-guard` if it grows) |
| 6 | Build | Each criterion: a failing test that names its ID (`<feature>/AC-01`), then the code, then green | `tdd` (`implementation` for non-testable glue; `debugging` when a failure is not understood) |
| 7 | Verify | Whole suite and project checks run in this session; a table of every criterion with evidence | `verification` |
| 8 | Review | Diff reviewed against the ticket and criteria; findings fixed or accepted | `code-review` (`security` for auth, input or secrets) |
| 9 | Ship | Only when the user asks: commit(s), PR text, ticket `state: done` | `delivery` / `finish-branch` |
| 10 | Learn | Knowledge current (`knowledge discover`, decisions/terms recorded); rework explained | `knowledge-update`, `retrospective` |

## Good habits in one chat

- **Start with a precise prompt.** For example: "Implement DU-FE-marketplace-launch-T03. Read the ticket item and
  its feature first; don't commit." Name the ticket ID, not just a description.
- **Start each step with the skill name** (`$tdd`, `$verification`). The agent then loads the right procedure
  instead of improvising.
- **One ticket per chat.** When the chat gets long or switches topic, end with `handoff` and start a fresh chat
  with that summary.
- **Gates beat speed:** criteria before code, a failing test before a fix, verification before "done", review
  before commit.
- **Evidence in the chat.** Ask for the command output behind every "passes". "NOT RUN" is an honest answer;
  "should work" is not.
- **Changing a requirement is a spec change, not a code change.** If the ticket is wrong, go back to the to-spec
  skill (new revision); then re-cut the tickets.
- **Name the IDs.** Tests and code that mention `<feature>/AC-xx` make coverage visible in `ai-skills next`, the
  knowledge graph and the read-only view.
- **Ask `$next-step` between steps.** It checks the gates above against the project state and the chat.
