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
  call), the state cap (16), the step budget, or an analyzer exception turns textual
  `stash` plus `pop`/`apply` evidence anywhere in the whole original command, in either
  order, into a refused restore instead of dropping it. Evidence also counts after quote
  and backslash removal, ANSI-C decoding, and substituting literal `NAME=value` bindings
  (so `OP=pop; S=stash; … "$S" "$OP"`, `"$A$B"`, `st'a'sh` and `$'\x73tash'` count). An
  unrelated `pop` word next to a `stash` read in an uncertain command is therefore refused
  too (intentional conservative false positive). The whole command is checked, not only the fragment that failed, because
  a fragment's boundaries can themselves be wrong: in `echo "$(case a in a) git -C <wt>
  stash pop;; esac)"` the paren matcher stops at the case pattern's `)`, so the restore lies
  outside the failed fragment.

- resolves known scalar command names and Git argv (including the stash subcommand and
  operation), not merely paths. Unquoted empty, whitespace-containing or glob-containing
  expansions and unquoted brace expansion (`{stash,pop}`, `{1..3}`) are ambiguous; function
  positional argv (`"$@"`) is not interpreted;
- treats unsupported wrapper options/environment (`env -C`, `nice -n`, `stdbuf -oL`,
  `command -p`, `exec -a`) and `coproc` as uncertain, including absolute wrapper paths
  and scalar wrapper names. Simple option-free wrappers remain supported. `timeout` is
  parsed exactly (`--foreground`, `--preserve-status`, `-v`, `-k`/`--kill-after`,
  `-s`/`--signal` in both spellings, then a numeric duration); any other shape is
  unsupported. `builtin` and `command` run the builtin that follows (`builtin cd X` changes
  cwd). Command runners (`xargs`, `parallel`, `find`, `flock`, `setsid`, `ionice`, `nsenter`,
  `su`, `watch`, `script`, `uv`, ...) are treated as running any suffix of their argv;
  `xargs`/`parallel`/`find` also append an unknown word, and whitespace-bearing arguments
  are analyzed as shell strings;
- re-analyzes an unsupported wrapper's argv and an unknown command name's argv: every
  argv suffix (and, for an unknown name, the argv as git's) is analyzed as a command whose
  repo is unknown. ANSI-C text (`$'...'`) is likewise re-analyzed after decoding to single
  quotes, repo unknown. These shadow passes only add refusals, so adding an unsupported
  wrapper or an ANSI-C word never turns a refused restore into an allow;
- treats Git argv conservatively: an unknown word in the subcommand position, an unknown
  global option (unknown arity), a splittable unknown option argument, or an alias that
  `-c alias.*`/`--config-env`/`GIT_CONFIG_*` may define (any non-builtin subcommand) makes
  the subcommand unknown; then any unknown word or restore evidence after it is a restore
  in an unknown repo. Known global options (`--namespace`, `--attr-source`,
  `--shallow-file`, `--super-prefix`, `--config-env`, `-c`, `-C`, `--no-pager`, ...) are
  parsed with their real arity. After `stash`, an unknown first positional (skipping
  `-m`/`--message` values) is a possible `pop`; an unknown ref makes the target unknown.
  The textual fallback mirrors this: a `stash` followed by an unresolved word, or `git`
  followed by an unresolved subcommand, counts as restore evidence;
- resolves a shell function by the expanded command name before wrapper stripping, as
  bash does: a function named `env`, `timeout`, `nice`, `sudo`, even `command` or `exec`,
  runs its body. A wrapped name (`command git`, `env git`, `builtin cd`) still bypasses
  functions;
- rejects ANSI-C quoting (`$'...'`) conservatively before lexing. This can refuse even
  harmless occurrences alongside textual restores; escape-aware quoting is not modeled;
- tracks Bash's logical cwd (`cd -L`, the default) separately from the physical one. When
  a `cd`/`pushd` target with `..` resolves differently logically and physically (a symlink
  in the path), the destination is unknown; `cd -P` uses the physical result. Prefix
  bindings of `HOME`, `OLDPWD` and `CDPATH` are applied to that `cd` only;
- treats a nonempty/unknown `CDPATH` on relative `cd` as an unknown destination, including
  prefix assignments. Absolute paths and explicit `./` or `../` paths remain resolvable;
- treats exported or prefix `GIT_DIR`/`GIT_WORK_TREE`/`GIT_COMMON_DIR`, `-c core.worktree`/
  `core.bare`, and the CLI selectors
  `--git-dir`/`--work-tree` (both `=` and separate-argument spellings) and `--bare`, as
  unknown repository selection that stays unknown across any later absolute `git -C`
  (which changes cwd, not that selection). `set -a` (also `set -o allexport`, `set -eo
  allexport`, `shopt -so allexport`, `bash -a`) exports variables assigned after it; `set
  -k` moves `NAME=value` words anywhere in argv to the command's environment; after
  `declare -n` any assignment may set `IFS`, `HOME`, `CDPATH` or `GIT_DIR`;
- reads a shell's script the way bash does: option clusters (`bash -ec`, `bash -lc`),
  `-o`/`-O` arguments and `--` before the `-c` script. A shell that reads its script from a
  pipe, a file or a process substitution (`… | bash`, `bash x.sh`, `bash <(…)`), and
  `source`/`.` of anything, is uncertain: any textual restore in the whole command is
  refused. A `stash`-free `pop`/`apply` word piped into a shell counts too (the earlier
  command may build `stash` from pieces), also when a here-doc feeds another fd; so do
  `perl -e`, `python -c`, `node -e` and `awk` programs;
- splits unquoted scalars on a changed or unknown `IFS`, and analyzes text with
  backslash-newline both joined (as bash tokenizes it) and as written (a backslash in a
  comment does not join), keeping every refusal of either reading;
- treats the dashed program (`git-stash pop`, `$(git --exec-path)/git-stash`) as git, and
  `hash -p PATH NAME` (or `BASH_CMDS[NAME]=PATH`) as NAME running PATH *and* as NAME
  itself, since `hash -r`, `hash -d`, a `PATH` assignment or `set +h` drop the entry;
- treats any other program whose expanded argv names git or a shell, or holds a word this
  command made unknown (`prlimit $G …`, `setpriv … $(git --exec-path)/git-stash`,
  `busybox sh -c …`, `nix develop -c git …`) as a runner: each argv suffix is analyzed
  with an unknown repo. Its string arguments that may hold git are analyzed as shell code
  it may run (`tmux new 'git …'`, `make --eval`, `vim -c '!…'`, `npx -c`). Data-only
  programs (`echo`, `grep`, `sed` without its `e` command, `gh`, `jq`, `cat`, `cp`, …)
  are exempt, so `gh pr create --body "git stash pop"` stays allowed;
- analyzes code bash or Git runs implicitly: assignment values (`PS4`, `${x@P}`,
  `(( x ))`), `command_not_found_handle`, `mapfile -C`, `sed` `e`, `perl`/`python`/`node`
  /`awk` program text in argv or a pipe, Git `-c`/`git config` program settings and
  `GIT_EXTERNAL_DIFF`/`GIT_EDITOR`/`GIT_PAGER`-style variables (analyzed as shell code
  with an unknown repo); `core.hooksPath`, `BASH_ENV`/`ENV`, `--rcfile` and login or
  interactive shells with a changed `HOME` make the whole command uncertain;
- decodes `\xHH`, octal and `\uHHHH` escapes in restore evidence (`printf '\x73tash' | bash`);
- treats `-c include.*`/`includeIf.*`, `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`/
  `GIT_CONFIG`/`XDG_CONFIG_HOME` and a changed `HOME` like `-c alias.*`: a config file may
  define an alias, so a non-builtin subcommand is unknown; so does an earlier
  `git config alias.*`/`include.*` in the same command.

Known scalar same-branch restores and known single-worktree repos retain their previous
behavior. Unsupported forms intentionally may refuse safe restores (for example
`git -C $UNQUOTED stash list`, `git "$SUB" log`, `timeout 1e3 git … stash pop`, a
restore behind `xargs`/`flock`/`prlimit`/`nix develop -c` even in a same-branch repo,
any textual `stash … pop` in a command that also runs `bash x.sh`, `… | sh` or `source`,
a `pop`/`apply` word piped into a shell, an unknown `declare -n` target, or a data string
of an unmodeled program that holds a restore command, such as `ssh host "git stash pop"`).
This remains bounded shell analysis, not a shell sandbox. Known limits: shell aliases
defined before the command (an `alias` in the command makes every unmodeled name a
possible git),
Git aliases already stored in repository/user config, `shopt -s lastpipe`, `set -e`,
exported functions, inherited state of a persistent terminal (logical `PWD`, exported
`IFS`/`CDPATH`/`GIT_DIR`, `set -a`), scripts whose text is not in the command (a file
written earlier, then run), restore text split by format strings into a file that a
hook or script later runs, sourced file contents, a `case` pattern inside `$(...)` (falls back to
fail-closed), split scalars built only from non-literal sources under a parse failure, and
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

Composition regressions run each refused shape in bash against the real fixture after
checking the guard left the stash and files untouched:
`test_uncertain_fallback_is_order_and_spelling_independent` (reversed scalar order under
unsupported wrappers and ANSI-C, split scalars, quoted and escaped spellings),
`test_functions_resolve_before_wrapper_names` (functions named after wrappers incl.
`timeout`/`nice`/`command`/`exec`, and `command` bypassing a `git` function), and
`test_cli_alternate_selection_is_sticky_across_C` (both option spellings, either order,
`-C` before or after). `test_function_precedence_allow_controls` and
`test_alternate_selection_controls` keep same-branch, single-worktree, redirection and
non-restoring shapes allowed.

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

Review then found three composition gaps. The uncertainty fallback matched only
`stash … pop` in that order, so reversed scalar assignments (`OP=pop; S=stash`) behind an
unsupported wrapper or ANSI-C quote escaped it. Wrapper stripping ran before function
lookup, so `env() { …restore…; }; env true` was analyzed as the `true` command while bash
ran the body. `--git-dir`/`--work-tree` cleared the repo but a later absolute `-C` set it
again. Earlier tests exercised each feature alone (literal wrapper argv, a function named
`git`, environment selection), not these compositions; the regressions above do.

A second review found seven more real restores, all one root: trust was granted at an
unrecognized boundary, and the uncertainty fallback was weaker than ordinary analysis.
Long `timeout` options did not match the short-flag pattern, so `--foreground` became the
command and the Git call was dropped; unquoted braces were not an unsupported expansion;
`builtin` was not a wrapper, so `builtin cd X` changed nothing; an unknown Git global
option's argument (`--namespace foo`) was read as the subcommand; and an unknown op value
(`printf -v OP …; … stash "$OP"`) was refused directly but allowed once a wrapper option or
an ANSI-C word sent the command to the text fallback, which needed a literal `pop`. Tests
varied literal spellings and orders but never crossed unknown values with uncertainty.
The fix parses `timeout` exactly, treats braces/unknown options/unknown subcommands as
unknown, models `builtin`, and re-analyzes uncertain fragments with an unknown repo so
uncertainty is monotonic. Tracing nearby parameters found the same class in command
runners (`xargs`, `flock`, `find -exec`), `-c alias.*`, prefix `OLDPWD`/`HOME` for `cd`, and
logical `cd` through a symlink followed by `..`.
`test_unknown_argv_and_wrapper_restores_are_refused` and
`test_logical_cd_through_symlink_is_not_resolved_physically` run each refused shape in bash
after checking the guard left the fixture untouched; `test_unsupported_argv_forms_fail_closed`
covers shapes bash would reject; `test_added_uncertainty_never_turns_a_refusal_into_allow`
crosses refused bases with wrapper/ANSI-C/runner decorations; `test_round3_allow_controls`
and `test_logical_cd_allow_controls` keep same-branch, single-worktree, redirection and
non-restoring shapes allowed.

A third review found nineteen more real restores; all already existed before this work.
Root: the analyzer still granted trust wherever it did not model a construct. A program
it did not know (`prlimit`, `eatmydata`, `git-stash`, a `hash -p` name) or a shell whose
script it did not read (`bash -ec`, `… | bash`, `bash <(…)`) was treated as running no
git, and state it did not track (`IFS`, `set -a`, backslash-newline, config include files)
was treated as default. Tests only covered constructs that were modeled. The fix makes
the unmodeled cases uncertain or analyzes them conservatively (see the list above).
`test_round4_restores_are_refused` runs each shape in bash after checking the guard left
the fixture untouched; `test_round4_comment_does_not_continue_line` covers the comment
reading; `test_round4_allow_controls` keeps same-branch, single-worktree and non-restoring
shapes through the new paths allowed.

A fourth (adversarial) pass over that fix found six regressions and seventeen more real
restores in the same classes. Root: the new modeling assumed one bash semantics where bash
has several (a hash entry can be dropped, `set -a` exports only later assignments, a
here-doc can feed another fd), and evidence matching missed `pop\n`. The hash table is
now analyzed both ways, allexport/keyword/nameref are tracked per assignment, and piped
text counts next to any here-doc. `test_round5_restores_are_refused` reproduces each in
bash; `test_round5_allow_controls` covers the matching non-restoring shapes.

A further adversarial pass found real restores through programs and settings that run a
string (`sed e`, `make --eval`, `vim -c '!…'`, Git `diff.external`/`core.hooksPath`,
`GIT_EXTERNAL_DIFF`), code bash runs implicitly (`PS4`, `${x@P}`, arithmetic subscripts,
`command_not_found_handle`, `mapfile -C`, `BASH_ENV`, `--rcfile`) and escape-encoded text
piped to bash. Root: a string was trusted as data unless the analyzer knew the program
that runs it. `test_round6_restores_are_refused` reproduces each in bash; the round-5
allow controls now also keep `python -c`, `make "$TARGET"`, `gh pr create --body`, `sed`
substitutions mentioning a restore, editor/pager settings and `git config alias.st
status` allowed.
