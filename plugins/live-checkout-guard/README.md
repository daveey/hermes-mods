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

## Archive-verified stash retirement

The maintained `reconcile.py` helper also supports stash retirement. This is a separate
mode from file reconciliation; it never applies a stash or writes checkout files, the
live index or HEAD. Raw `git stash` mutations remain refused. Raw `update-ref` on
`refs/stash` / `refs/hermes-retire/*` (and all stdin update-ref batches), and
`reflog delete|expire|drop|write` on those refs or `--all`, are also refused in the
live checkout. The helper is an ordinary maintained script, not a blanket shell
allowlist or a guard-disable switch.

Use Python 3 on Linux or macOS and a Git version supporting prepared `update-ref`
transactions and `rev-parse --show-ref-format` (tested on Git 2.55 on Linux; the
complete helper has not yet been exercised on macOS). Only the files ref backend
is supported; shallow repositories are refused.

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
partial commits and unchanged checkout/index/HEAD. `tests/test_retirement_cli.py`
exercises all three CLI modes plus terminal/execute-code guard checks. Existing
`tests_reconcile.py` retains the separate file-reconciliation constraints.

## Limitations (best-effort, not a sandbox)

- Lexical analysis only. Variables/command substitution (`cd $X`, `> $(…)`, `"$DIR"/f`), globs, `xargs`, `find -exec`,
  exported `GIT_DIR` (set in an earlier segment/call), aliases, functions, scripts that write on their own (`make`, `npm`,
  editors, `python script.py`) are not seen.
- `execute_code` with non-literal paths/commands (built strings, loop variables, imports of other modules) is not inspected;
  only literal arguments are. A tool called by name through another route (e.g. an MCP tool that writes files) is not covered.
- Subshell/`cd` scoping is flattened: `(cd root; …)` leaves later segments thinking cwd is root (can over-block);
  heredoc bodies are scanned as if they were commands (can over-block text containing `git commit` while cwd is root).
- Wrapper option tables cover the common wrappers above; an unknown wrapper or option that takes a value can still hide
  the real command.
- The session cwd is read only from a core already loaded in the hook's process (it always is inside Hermes);
  elsewhere the hook falls back to `$TERMINAL_CWD`/process cwd.
- Tests: `scripts/test-all -k live-checkout-guard`, or `python -m pytest -q -p no:cacheprovider tests_guard.py` here.
  `test_real_core_registered_hook_uses_persisted_terminal_cwd` runs `_core_probe.py` under a Hermes runtime python
  (`$HERMES_CORE_PYTHON`, else `~/.hermes/installs/*/environments/*/venv/bin/python`) against `~/.hermes/hermes-agent`
  (`$HERMES_AGENT_DIR`); it skips when none can import the core.
