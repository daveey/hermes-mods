# stash-worktree-guard

`refs/stash` is one ref per repository. Every linked worktree shares it, so two agents that
`git stash` and `git stash pop` at the same time swap diffs: each pop applies the other's patch.

This plugin:

- appends a `[stash-worktree-guard]` warning to terminal output when the command ran `git stash`
  (push, pop, apply, list, no-arg, and the other subcommands) inside a repo with more than one
  worktree, and says to use a patch file instead;
- blocks `git stash pop` and `git stash apply` before they run when that stash's branch (from
  `WIP on <branch>:` / `On <branch>:`) is not the current branch. A subject that does not name a
  branch is blocked too. Same-branch pop is allowed; the warning still fires. A repo with one
  worktree is not touched.

`STASH_WORKTREE_GUARD=off` in the command skips both. Terminal, process stdin, execute_code
(shell strings and `["git", ...]` argv lists) and nested `tool_call` are covered for the block.
The warning hook is `transform_terminal_output`, so it also sees background-process output.

Already-open sessions keep the plugin module they imported. A new `hermes chat` picks this up.
