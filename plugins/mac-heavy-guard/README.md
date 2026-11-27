# mac-heavy-guard

Blocks recognized heavy builds, whole test suites and requirements installs on macOS.
Compound-command positions (`if`, loops, groups, case arms) use the same classifier as
plain commands; quoted keywords and loop/case data are not executable positions.
Pip's attached `-rFILE` and Cargo's `--profile release` are equivalent to their separated
and `--profile=release` forms.

SSH remote commands stay remote. Explicit `ProxyCommand`/`LocalCommand` options and
unquoted-heredoc command substitutions run locally and are inspected. Heredoc bodies
are exempt only for the strict remote-stdin shell form with data-only downstream
readers and no detected SSH remapping. This is a static load-policy aid, not a shell
sandbox: implicit SSH config, arbitrary variable evaluation and read/execute races
are not contained.

Local shell scripts are inspected up to 64 KiB and two script levels. An oversized
recognized script or an existing third-level shell script now requires an explicit
`MAC_HEAVY_OK=1` opt-out rather than silently accepting uninspected suffixes. Explicit
`bash`/`source` execution does not trust a different interpreter in the file's shebang.
Missing files and directly executed non-shell files retain their existing behavior.

## Tests

From the repository root: `python3 scripts/test-all --no-core -k mac-heavy-guard`.
This runs `tests_guard.py`, `tests/` and repository guards. Direct plugin-only runner:
`python3 -m pytest -q -p no:cacheprovider tests_guard.py tests` from this directory.
Use an isolated `HOME`, `HERMES_HOME` and `TMPDIR` for fixture writes.

Source-review C1/C2/C3/C4 regressions: `tests/test_source_review.py` (compound syntax,
equivalent flags, local SSH/heredoc execution and script limits, with data/remote/opt-out
controls). Synthetic commands are analyzed, not executed.

## Decision-module updates in running workers

Once this reload boundary has been activated, running workers pick up changes to
`guard.py` on their next tool call, without exiting. The hook stats its nanosecond
mtime and size before computing a verdict; unchanged code is not re-read. A changed
file is executed into a fresh module, then swapped only after successful loading.
A syntax/load error retains the last good decisions and logs one warning per changed
fingerprint. Successful reloads log `mac-heavy-guard: reloaded guard.py`.
`__init__.py`, hook registration and tool schemas are **not** hot-reloaded by this
boundary; their existing activation paths still apply.

Regression coverage: `tests/test_guard_decision_reload.py` at the repository root
loads temporary plugin copies and exercises the registered pre-tool hook across
allowed-to-refused edits, syntax errors, recovery and same-second/same-size edits.
