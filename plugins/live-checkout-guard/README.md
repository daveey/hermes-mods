# live-checkout-guard

Keeps agents from editing the **live checkout** (default `~/code/hermes-mods`, override with
`LIVE_CHECKOUT_ROOT`) in place. That tree is what the sync job and every other agent load from; edits there
race with the sync and with each other. Work goes in a detached worktree; the refusal message prints the recipe:

```
git -C ~/code/hermes-mods fetch origin
git -C ~/code/hermes-mods worktree add --detach ~/code/wt-$(date +%s) origin/main
# edit, test, commit in the worktree
git push origin HEAD:main
git -C ~/code/hermes-mods worktree remove <worktree>
```

Kill switch: `LIVE_CHECKOUT_GUARD=0`. Paths are compared after `realpath`, so symlinks into the root
(e.g. `~/.hermes/plugins/<name>`) count as inside it; worktrees outside the root are free.

## Behavior

- `patch` / `write_file` (Hermes, also `mcp__` names; replace mode and V4A `*** Update/Add/Delete File` / `Move to`):
  refused if any target resolves into the root.
- Starting cwd: the call's `workdir`, else the session's **recorded terminal cwd** from the core
  (`tools.terminal_tool.get_session_cwd`, keyed by the hook's `task_id` / approval session key, the same record the
  terminal and file tools use, so a `cd` in an earlier call counts), else `$TERMINAL_CWD`, else the process cwd.
- `terminal` (also `process`): per command segment (`;`, `&&`, `||`, `|`, newlines, `bash -c '…'` including clustered
  `bash -lc` / `bash -l -c` / `bash -o pipefail -c` for bash/sh/zsh/dash/ksh, `eval`), tracking
  `workdir`/`cd`/`pushd`/`git -C`. Wrappers are stripped with their option values: `sudo -u USER`, `nice -n 5`,
  `env -u VAR`, `timeout [-s SIG] DURATION`, `ionice`, `time -o`, `stdbuf`, `nohup`, `exec`, …; `env -C DIR` /
  `sudo -D DIR` move the cwd for that one command; `env -S 'cmd'` is checked as a command.
  - git `commit stash rebase reset checkout restore apply am cherry-pick merge switch pull revert clean` refused in the
    root, also when the repo is chosen with `--git-dir`/`--work-tree` or `GIT_DIR=`/`GIT_WORK_TREE=` prefixes.
    `git pull` is always refused there. The only update allowed is `git merge --ff-only origin/main`, and **only when
    the tree is clean** (no modified or untracked files). Also allowed: `stash list|show`, reads, `fetch`,
    `worktree add`, `add`, `branch`, …
  - `sed -i`/`--in-place`, `perl -i`, `>`, `>>`, `2>`, `&>`, `tee`, `rm mv touch truncate mkdir chmod unlink`,
    `cp/install/rsync` into the root: refused. `ln` is refused only when the link it creates lands in the root (last
    operand, `-t DIR`, or the cwd for a single operand); `ln -s <root>/f elsewhere/link` is fine. Reading from the
    root and writing elsewhere is fine.
  - `hermes-sync` is always allowed.
- `execute_code`: the code is parsed with `ast`; calls with **literal** arguments are checked: `terminal()`,
  `write_file()`, `patch()` (keyword or positional path), `subprocess.*`, `os.system/popen`,
  `open(path, 'w'|'a'|'x'|'+')`, `Path('…').write_text/write_bytes/touch/unlink/mkdir/rename/replace`.
  `hermes_tools` calls made from the sandbox (`terminal`, `write_file`, `patch`, …) are dispatched by the core RPC
  through `handle_function_call`, which fires `pre_tool_call` again with the computed arguments, so those are checked
  even when built dynamically. Plain Python (`subprocess`, `open`, `os.*`) with non-literal arguments is not: this
  guard is not a sandbox for arbitrary code.
- `tool_call` / `mcp__tool_call` batches are unwrapped and each call checked.
- `skill_manage` is not touched (it is the sanctioned way to patch skills in place).
- Claude Code: `claude_hook.py` is a PreToolUse hook for `Bash` and `Write|Edit|MultiEdit|NotebookEdit`.
  Install: `python3 plugins/live-checkout-guard/claude_hook.py --install [--settings PATH]` (idempotent; default
  `~/.claude/settings.json`). Exit 2 blocks; bad input fails open.

## Fenced core updates

Agent `terminal` and literal `execute_code` shell calls may not run a mutating
`hermes update`, locally or through SSH. Bare executables, absolute/tilde paths,
`python -m hermes_cli.main`, profile selection, shell `-c`/`eval`, and the common
wrappers above are recognized. This is independent of the command's cwd.

Core updates belong to each host's fenced route: the hub's
`hermes-core-update.timer` (every 10 minutes, drain-first and batched) and
`hermes-sync` drain windows on Macs. Direct updates can bypass drain intent,
restart active work, and contend with the scheduled updater. A card waiting for a
commit should use a scheduled wake check, substituting its actual SSH destination:

```sh
ssh <hub> 'git -C ~/.hermes/hermes-agent merge-base --is-ancestor <sha> HEAD'
```

Allowed read-only flags, verified against `hermes update --help`: `-h`, `--help`,
`--check`, `--plan`, `--list-venv-holders`, `--install-id`. Neither `--yes` nor
`--no-gateway-restart` is read-only. Grep patterns, SQLite LIKE strings, echo text
and heredocs written as data do not trigger the update rule; heredocs/herestrings
fed to a shell or SSH are checked as executable input.

For a **user-approved update only**, prefix the specific command with
`HERMES_UPDATE_OK=1`. It can prefix the updater, its SSH command, or its shell
wrapper. It does not authorize subsequent commands after `;` or `&&`, does not
read an inherited environment value, and does not bypass checkout-write checks.

Tests: `tests_update.py` exercises terminal and execute_code for blocked and
allowed forms, including both incident SSH spellings, every read-only flag,
text-only mentions, shell input, and override scoping. Run
`scripts/test-all -k live-checkout-guard`.

## Archive-verified stash retirement

The maintained `reconcile.py` helper also supports stash retirement. This is a separate
mode from file reconciliation; it never applies a stash or writes checkout files, the
live index or HEAD. Raw `git stash` mutations remain refused. Raw `update-ref` on
`refs/stash` / `refs/hermes-retire/*` (and all stdin update-ref batches), and
`reflog delete|expire|drop|write` on those refs or `--all`, are also refused in the
live checkout. The helper is an ordinary maintained script, not a blanket shell
allowlist or a guard-disable switch.

Use Python 3.9 or newer on Linux or macOS (an unqualified `python3` is 3.9 on stock
macOS; older interpreters are refused by `reconcile.py` with exit 2 and by
`stash_retirement.py` at import, before the lock or any archive/ref write) and a Git
version supporting prepared `update-ref` transactions and `rev-parse
--show-ref-format` (tested on Git 2.55 on Linux, and the full helper test suite on
macOS with Apple Git 2.54 and `/usr/bin/python3` 3.9.6). Only the files ref backend
is supported; shallow repositories
are refused. The `live-checkout-guard` GitHub workflow runs the helper suites on
Python 3.9 and 3.14.

The existing archive directory must be **outside** the checkout and Git directory.
Its `manifest.json` is a JSON list with one row for **every** current stash:
`{"stash":"FULL_OID","patch":"relative.patch","bytes":123,"sha256":"SHA256"}`.
Each patch must be the complete `git stash show -p --binary --include-untracked`
export. Preparation rejects missing or noncanonical bytes rather than regenerating
those patches. It adds an index-parent patch and a self-contained Git bundle per
entry, verifies each bundle by importing it into an empty scratch repository, and
writes `retirement-manifest.json`. Bundles include full base history and may be
large; keep this archive private wherever the repository itself is private.

```sh
python3 ~/code/hermes-mods/plugins/live-checkout-guard/reconcile.py \
  --root ~/code/hermes-mods --archive /absolute/verified-archive \
  --prepare-stash-retirement
python3 ~/code/hermes-mods/plugins/live-checkout-guard/reconcile.py \
  --root ~/code/hermes-mods --archive /absolute/verified-archive \
  --retire-stashes --oid FULL_OID_1 --oid FULL_OID_2
```

Select only the full lowercase OIDs whose disposition is approved. No date or
position selector is accepted. Preparation binds repository identity and the full
ordered stack; retirement re-verifies every archive, including non-target entries.
A prepared native Git transaction locks the stash ref, compares the stack under
that lock, deletes the old stack and creates recovery pins. Unrelated entries are
re-stored oldest first with their OID, parents and subject unchanged; reflog times
and actor identity change. Concurrent entries arriving after commit are preserved.
The helper holds `$HERMES_HOME/.hermes-sync.lock` throughout (default `~/.hermes`),
and passes that lock to Git children so an orphaned child prevents premature recovery.
Use the same Hermes home as the host's sync runner.

Unambiguous interrupted operations recover on the next prepare/retire, or explicitly:

```sh
python3 ~/code/hermes-mods/plugins/live-checkout-guard/reconcile.py \
  --root ~/code/hermes-mods --archive /absolute/verified-archive \
  --recover-stash-retirement
```

Recovery never guesses that a missing commit marker means abort. Ambiguous partial
commits, broken refs and native lock files refuse with pins retained. Do not remove
those pins or lock files blindly. A handled Ctrl-C or timeout during restore or
pin cleanup can kill that Git subprocess and leave native locks; the prepared
transaction child itself is never killed by the helper. A torn journal line,
missing commit evidence or partial commit also refuses rather than guessing.
There is deliberately no force/resolve mode: preserve the archive, journal and
pins, inspect the reported transaction and active Git processes, and route an
ambiguous state to a separately reviewed maintenance repair. Do not edit the
journal or bypass the live-checkout guard to force progress.
Git's files backend is not power-loss atomic;
the helper fsyncs archives, journal, refs and directories and fails closed on
ambiguous state, but cannot promise filesystem durability beyond fsync. Successful
retirements append OIDs and patch/index/bundle SHA-256s to
`stash-retirement-audit.jsonl`; `stash-retirement-journal.jsonl` drives recovery.
Audit events are at-least-once across interrupted writes; use transaction ID and
OID to deduplicate. The source owner must read back the stash stack and unchanged
checkout/index/HEAD after running the command. This helper does not authorize any
stash's disposition by itself.

Tests: `tests/test_stash_retirement.py` exercises archive rejection, independent
bundle recovery after GC, native writer races, lost replies, process death,
partial commits and unchanged checkout/index/HEAD; `TransactionPipeLifecycleTests`
covers repeated EOF/wait, retry after a wait timeout (child left alive, never
killed), stderr retention and no use of `Popen.communicate` (on Python 3.9 it
flushed the already-closed stdin after a committed deletion, then failed again on
retry). `tests/test_retirement_cli.py` exercises all three CLI modes on the running
interpreter and on Python 3.9 (`HERMES_MODS_PY39` or `python3.9`; skipped locally
when absent, a failure under `CI`), the pre-mutation old-interpreter refusal, plus
terminal/execute-code guard checks. Existing
`tests_reconcile.py` retains the separate file-reconciliation constraints.

## Limitations (best-effort, not a sandbox)

- Lexical analysis only. Variables/command substitution (`cd $X`, `> $(…)`, `"$DIR"/f`), globs, `xargs`, `find -exec`,
  exported `GIT_DIR` (set in an earlier segment/call), aliases, functions, scripts that write on their own (`make`, `npm`,
  editors, `python script.py`) are not seen.
- `execute_code` with non-literal paths/commands (built strings, loop variables, imports of other modules) is not inspected;
  only literal arguments are. A tool called by name through another route (e.g. an MCP tool that writes files) is not covered.
- Subshell/`cd` scoping is flattened: `(cd root; …)` leaves later segments thinking cwd is root (can over-block);
  checkout-write checks scan heredoc bodies as if they were commands (can over-block text containing
  `git commit` while cwd is root). The separate update rule skips data heredocs. It remains lexical:
  unusual shell grammars, arbitrary scripts and dynamic commands are not a security boundary.
- Wrapper option tables cover the common wrappers above; an unknown wrapper or option that takes a value can still hide
  the real command.
- The session cwd is read only from a core already loaded in the hook's process (it always is inside Hermes);
  elsewhere the hook falls back to `$TERMINAL_CWD`/process cwd.
- Tests: `scripts/test-all -k live-checkout-guard`, or `python -m pytest -q -p no:cacheprovider tests_guard.py` here.
  `test_real_core_registered_hook_uses_persisted_terminal_cwd` runs `_core_probe.py` under a Hermes runtime python
  (`$HERMES_CORE_PYTHON`, else `~/.hermes/installs/*/environments/*/venv/bin/python`) against `~/.hermes/hermes-agent`
  (`$HERMES_AGENT_DIR`); it skips when none can import the core.
