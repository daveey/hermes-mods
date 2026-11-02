# cpu-load-guard

## Shell burner regression contract

`tests_guard.py` generates a must-refuse cross-product of burner payloads,
wrappers and command positions, plus literal background counts and the historical
refusal cases, with `CPU_LOAD_GUARD_NCPU=16`. It also preserves a must-allow corpus
for SSH option polling, argument text, small worker counts and `yes | cmd`.
The corpus is analyzed by `check_shell()` only; burners are never executed.

The command-position prefix recognizes brace groups, negation, `elif`, `command`,
bare assignments, the supported `nice` increment forms, separate timeout option
values and absolute burner paths. Both shell and background matchers recognize
`openssl speed` (the existing worker threshold still applies).

Failure chain: narrowing the prefix to avoid matching `BatchMode=yes` omitted
valid command positions and wrappers, so those burners no longer matched.
The previous targeted examples all passed because no cross-product refusal
contract preserved those forms. The new generated corpus makes that narrowing
visible alongside the legitimate polling cases rather than trading one for the
other. Run `python3 scripts/test-all -k cpu-load-guard` from the repository root;
the direct suite is `tests_guard.py tests_runtime.py tests_worktree.py`.

## Safe maintenance in linked worktrees

File writes and patches to tracked `plugins/cpu-load-guard/` files in registered
worktrees of the installed plugin's repository are exempt from fixture scanning.
Identity requires the same canonical Git common directory, a matching repository
root and a registered worktree path; a matching directory name or cloned remote
alone is insufficient. Inherited `GIT_*` overrides cannot establish identity.
Untracked scripts and symlink escapes retain normal write checks. Git errors retain
normal checks too. This does not exempt terminal or execute_code execution.

`tests_worktree.py` uses isolated real Git repositories to cover direct writes,
replacement and V4A patches, deferred patches, relative paths, symlink aliases and
escapes, lookalikes, independent clones, forged gitfiles, Git failure and retained
terminal/execute_code execution checks. Burner fixtures are never executed.

## Decision-module updates in running workers

Once this reload boundary has been activated, running workers pick up changes to
`guard.py` on their next tool call, without exiting. The hook stats its nanosecond
mtime and size before computing a verdict; unchanged code is not re-read. A changed
file is executed into a fresh module, then swapped only after successful loading.
A syntax/load error retains the last good decisions and logs one warning per changed
fingerprint. Successful reloads log `cpu-load-guard: reloaded guard.py`.
`__init__.py`, hook registration and tool schemas are **not** hot-reloaded by this
boundary; their existing activation paths still apply.

Regression coverage: `tests/test_guard_decision_reload.py` at the repository root
loads temporary plugin copies and exercises the registered pre-tool hook across
allowed-to-refused edits, syntax errors, recovery and same-second/same-size edits.
