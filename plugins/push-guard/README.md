# push-guard

## Decision-module updates in running workers

Once this reload boundary has been activated, running workers pick up changes to
`guard.py` on their next tool call, without exiting. The hook stats its nanosecond
mtime and size before computing a verdict; unchanged code is not re-read. A changed
file is executed into a fresh module, then swapped only after successful loading.
A syntax/load error retains the last good decisions and logs one warning per changed
fingerprint. Successful reloads log `push-guard: reloaded guard.py`.
`__init__.py`, hook registration and tool schemas are **not** hot-reloaded by this
boundary; their existing activation paths still apply.

Regression coverage: `tests/test_guard_decision_reload.py` at the repository root
loads temporary plugin copies and exercises the registered pre-tool hook across
allowed-to-refused edits, syntax errors, recovery and same-second/same-size edits.
