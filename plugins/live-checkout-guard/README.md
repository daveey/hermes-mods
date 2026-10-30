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
