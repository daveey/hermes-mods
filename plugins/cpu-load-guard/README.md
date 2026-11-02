# cpu-load-guard

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
