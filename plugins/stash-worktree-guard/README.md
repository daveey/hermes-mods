# stash-worktree-guard

`refs/stash` is one ref per repository. Every linked worktree shares it, so two agents that
`git stash` and `git stash pop` at the same time can swap diffs.

This plugin:

- appends a `[stash-worktree-guard]` warning to terminal output for `git stash`
  (including `list`) in a repo with more than one worktree;
- blocks `git stash pop` and `git stash apply` before execution when the stash's branch
  (`WIP on <branch>:` / `On <branch>:`) differs from the current branch, or its subject
  does not name a branch;
- resolves scalar assignments made earlier in the command, `$HOME`, `$TMPDIR`, and `~`
  in `cd` and `git -C` paths. It preserves quoting and tracks `PWD` from the tool's cwd,
  not the agent process's potentially stale environment;
- fails closed for restoring operations when the repo cannot be resolved or read.
  Unknown variables, command substitution, and unsupported expansions are not executed
  to discover the path. Read-only stash commands and pushes are not refused on that basis.

Same-branch restores and known single-worktree repos retain their previous behavior.
This remains bounded shell analysis, not a shell sandbox; arbitrary shell programs and
Python-computed command strings are not completely interpreted.

`STASH_WORKTREE_GUARD=off` in the command skips both paths. The pre-tool hook covers
terminal, process stdin, execute_code (literal shell strings and git argv lists), and
nested `tool_call`. It runs before the terminal handler can promote a timeout over
600 seconds to a background process. Enforcement does not depend on a subsequent
`transform_terminal_output` warning or background completion notification.

Already-open sessions keep the plugin module they imported. A fresh session picks up
new code; updating the files is not proof an existing session refreshed.

## Tests

From the repository root: `scripts/test-all -k stash-worktree-guard`.

`tests_guard.py` uses real temporary Git repositories and linked worktrees. It exercises
cross-branch refusal through terminal, execute_code literals, and nested tool_call;
same-branch and single-worktree controls; the assignment → cd → stash push → pop shape
with `timeout=900`; `git -C "$M"`; HOME/TMPDIR/tilde; unresolved substitutions for pop/apply
versus non-restoring operations; unknown cwd recovery; variable scope, tracked PWD, and
single-quoted literal variables. Process-stdin dispatch has no dedicated test.

## Failure analysis

The original parser discarded assignment tokens. An unresolved `$WT` then became a
literal nonexistent path, whose failed worktree listing returned zero. Treating zero
like one bypassed the branch comparison. Original tests only supplied literal paths,
so they did not exercise this unknown-state transition. The fix retains unknown state,
refuses restores on unknown/unreadable repositories, and tests variable-based commands
before execution rather than relying on an after-the-fact warning.
