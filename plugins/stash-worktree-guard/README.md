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
- parses the command (stdlib only, never executed) into lists, `&&`/`||`, pipelines
  (`|`, `|&`), background `&`, `( )` subshells, `{ }` groups, `if`/`while`/`until`/`for`/`case`
  (`;;`, `;&`, `;;&`), functions, and heredocs. Redirections (`2>&1`, `>/dev/null 2>&1`,
  `&>/dev/null`) and quoted text are not separators. It tracks a bounded set of possible
  cwd/variable states: a restore must be safe in every state it may run in, so
  `false && cd X; git stash pop` is checked in both directories;
- scopes state: a `cd` inside `( )`, a pipeline element, or a background job does not change
  the parent's cwd; `{ }` and function calls do;
- inspects `$(...)`, backticks, `<(...)`/`>(...)` (quoted or not) in an isolated copy of the
  current state, and `bash -c`, `bash <<EOF`, `bash <<<`, `eval` with known words, and `trap`
  actions as nested scripts;
- invalidates what it does not model: `+=`, arrays, `unset`, `read`/`mapfile`/`printf -v`
  targets, `declare` with type flags, `popd` and stack forms of `pushd`, `source`/`.`, and
  `eval` with unknown words. An unknown command name followed by `stash` is a restore in an
  unknown repo;
- fails closed for restoring operations when the repo cannot be resolved or read.
  Unknown variables, command substitution, and unsupported expansions are not executed
  to discover the path. Read-only stash commands and pushes are not refused on that basis.
  A parse failure, the nesting limit (8 levels of `bash -c`/substitution/eval/function
  call), the state cap (16), the step budget, or an analyzer exception turns any textual
  `stash … pop|apply` anywhere in the whole original command into a refused restore instead
  of dropping it. The whole command is checked, not only the fragment that failed, because
  a fragment's boundaries can themselves be wrong: in `echo "$(case a in a) git -C <wt>
  stash pop;; esac)"` the paren matcher stops at the case pattern's `)`, so the restore lies
  outside the failed fragment.

Same-branch restores and known single-worktree repos retain their previous behavior.
This remains bounded shell analysis, not a shell sandbox. Known limits: aliases,
`CDPATH`, `shopt -s lastpipe`, `set -e`, exported functions, `coproc`, `xargs`/`find -exec`,
sourced file contents, a `case` pattern inside `$(...)` (falls back to fail-closed), and
Python-computed command strings are not interpreted; `cd` into an existing directory is
assumed to succeed.

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

`test_bounded_shell_state` (terminal) covers grouping, background/pipeline scoping, `|&`,
redirection allows, `+=`/`unset`/`declare -x`/`read`/`eval`/function invalidation, `popd`,
quoted and unquoted substitutions with inherited scope, `case` separators and ambiguity,
`&&`/`||`/`exit`, each with same-branch or single-worktree allow controls.
`test_nested_shell_exhaustion_fails_closed` covers the depth limit for `bash -c`,
substitutions and recursive functions. `test_other_shell_entry_points` covers heredoc and
herestring shells, unknown command names, `trap`, `source`, loops, and a parse failure.
`test_execute_code_shell_state` covers subshell, background, substitution and redirection
shapes through execute_code. `test_quoted_case_substitution_fails_closed_terminal` and
`_execute_code` cover the quoted `case`-in-`$(...)` shape (the terminal test also runs it
in bash to prove it really pops into the other worktree); a non-restoring variant stays
allowed. `test_state_cap_collapses_to_unknown`, `test_step_budget_fails_closed` and
`test_unexpected_analyzer_exception_fails_closed` cover the three bounds, each with an
allow control. `mapfile`/`printf -v`/`typeset` invalidation has no dedicated test.

## Failure analysis

The original parser discarded assignment tokens. An unresolved `$WT` then became a
literal nonexistent path, whose failed worktree listing returned zero. Treating zero
like one bypassed the branch comparison. Original tests only supplied literal paths,
so they did not exercise this unknown-state transition. The fix retains unknown state,
refuses restores on unknown/unreadable repositories, and tests variable-based commands
before execution rather than relying on an after-the-fact warning.

The second gap had the same root: the analyzer split commands with a regex over
`; && || | &`. `(cd X && git stash pop)` kept `(cd` and `pop)` as words, so neither the
`cd` nor the `pop` was recognized; `cd X & …` and `cd X | …` leaked their cwd to the
parent; `2>&1` was split at its `&`; `x=$(git stash pop)` read as an assignment;
`+=`, `unset` and `popd` were ignored, keeping a stale safe path; nesting beyond 3 levels
returned no calls. The tests only used flat `;`/`&&` lists. The fix replaces the regex with
a parser that separates operators from redirections and quotes, scopes state per
construct, and fails closed whenever a bound or unsupported form is reached.
