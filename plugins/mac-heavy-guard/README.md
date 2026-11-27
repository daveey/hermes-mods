# mac-heavy-guard

Blocks recognized heavy builds, whole test suites and requirements installs on macOS.
Compound-command positions (`if`, loops, groups, case arms) use the same classifier as
plain commands; quoted keywords and loop/case data are not executable positions.
Pip's attached `-rFILE` and Cargo's `--profile release` are equivalent to their separated
and `--profile=release` forms.

SSH remote commands stay remote. Explicit `ProxyCommand`/`LocalCommand`/`KnownHostsCommand`
options (before or immediately after the host) and unquoted-heredoc command
substitutions run locally and are inspected. Heredoc bodies are exempt only for the
strict remote-stdin shell form with data-only downstream readers and no detected
SSH remapping. This intentionally holds complex remote heredocs too: remote `cd &&
bash -s`, `sudo bash -s`, `cat > file`, and `-J` are outside the exemption grammar,
even when they would never execute locally. Keeping the narrow, auditable grammar
avoids widening local-pipeline/remapping exemptions; this is a conservative load-policy
hold, not evidence that the body runs locally. For those forms, set `MAC_HEAVY_OK=1`
in the invoking guard process's environment (the exported opt-out); an inline
assignment on SSH alone does not exempt a separately scanned heredoc body. For a
per-command exception, prefix the held body line itself: `MAC_HEAVY_OK=1 pytest`.
This is a static load-policy aid, not a shell sandbox: implicit SSH config, arbitrary
variable evaluation and read/execute races are not contained.

Command substitutions are inspected at their textual position, including case/loop
headers, using a private subshell cwd: a substitution's `cd` cannot redirect earlier
or later parent script inspection. Substitution nesting beyond 32 levels requires
the exported opt-out rather than silently accepting uninspected code.

The bounded substitution scanner conservatively holds a `case` statement inside
a substitution (including benign case bodies): unparenthesized case-pattern `)`
is ambiguous to its character-level delimiter matcher. This includes `time`,
`coproc` and `function NAME` compound prefixes, including `time -p --` and
shell-word names (punctuation, quoted or escaped name parts). For the local guard, use the
exported opt-out for that form (tailnet policy does not honor this opt-out);
quoted `case` data and `echo case` remain data. This is an explicit
inspection hold, not a claim that every case body is heavy.
Class fallback: a `case` inside a substitution that follows an unmodeled construct (`coproc`, `function`,
`time`, `elif`, or `${...}` text) is refused unconditionally, with no heavy-word test (a raw-text test is
bypassed by quote splices, escapes and scripts). A `${...}` containing a parenthesis inside any substitution
(`$(echo ${x:-)})`) is held too, since the scanner would end the substitution at that `)`. False refusals:
such commands that only mention a heavy word as data, or contain no heavy command at all; use the local
exported `MAC_HEAVY_OK=1` opt-out.
The public `commands()` API still yields only simple argv lists, recursively
flattening substitution bodies for consumers such as tailnet. Inspection holds
raise `ValueError` there, so consumers refuse rather than treating private events
as executable argv. Internal cwd-sensitive scanning uses a separate event stream.

Local shell scripts are inspected up to 64 KiB and two script levels. An oversized
recognized script or an existing third-level shell script now requires an explicit
`MAC_HEAVY_OK=1` opt-out rather than silently accepting uninspected suffixes. This
also holds benign oversized helpers such as a sourced `nvm.sh` or `./configure`.
Explicit
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
