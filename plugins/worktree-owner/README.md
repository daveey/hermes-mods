# worktree-owner

Whoever creates a git worktree owns removing it.

An audit of one Mac found 630 linked worktrees across 20 repos (median age 15 days). Most were made by an agent
(`git worktree add ~/code/<repo>-<slug>`) that finished without `git worktree remove`. Kanban workers were one of
the main sources: the card ended, the dispatcher cleaned only the workspace it had created itself, and nothing knew
the worker's own worktrees belonged to the card.

## What it does

Two `post_tool_call` hooks. Neither one blocks or changes a tool call, and every error is swallowed and logged.

1. **Registration.** After a `terminal` or `execute_code` call whose text contains `worktree add`, the hook finds
   every per-worktree gitdir (`<repo>/.git/worktrees/<name>`) that was born during that call. It looks in repos
   under `~/code` (depth 1 and 2), `~/.hermes/hermes-agent` and the call's own repo. Each one gets a
   `hermes-owner.json` holding the Kanban card id (`HERMES_KANBAN_TASK`), the session id, the profile and the time.
   The marker lives inside git's own per-worktree dir, so it is never committed and it goes away with the worktree.
   Because the hook reads git's state rather than parsing the shell, `for b in ...; do git worktree add ...; done`
   fan-outs and trees nested inside a card's scratch workspace are covered. Attribution guard: a tree is claimed
   only if its directory name appears in the command or code, or, for `terminal` only, a shell loop (`for`, `while`,
   `xargs`, `parallel`) builds the paths from `$` variables. A tree that another agent creates in the same seconds is
   not claimed.
2. **Teardown.** Only a successful `kanban_complete` result for the worker's own task and run can start cleanup. A writer reservation on the exact worker board protects verification and removal: the task must be `done`, its current run cleared, and its latest run must match the worker and have ended `done/completed`. Missing, failed, rejected, foreign-board/task, stale-run and review results do nothing. Markers carry the canonical board DB and run ID; ambiguous legacy markers are preserved.
   Active child dependencies (even before workspace assignment), open-card workspace overlaps on that board and live process cwd paths preserve trees. Clean, pushed trees are removed without force, retaining their branches. Simple dirty/untracked/unpushed linked trees nested in the completed scratch workspace are archived before removal: a self-contained verified `unpushed.bundle`, binary `uncommitted.diff`, readable `untracked.tar.gz`, and `meta.json`. Archive failure preserves the source. Recover with `git clone <archive>/unpushed.bundle <restore>`, `git -C <restore> apply <archive>/uncommitted.diff` when present, then extract the untracked archive there.
   Staged changes, git-ignored files, nested Git entries, symlinks, special files and scan uncertainty are conservatively preserved, not force-removed. Rebuildable caches are the one exception: `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `node_modules`, `*.egg-info` and any directory holding a `CACHEDIR.TAG` do not keep a tree (and the symlink scan does not descend into them); any other ignored file (`.env`, notes) still does. A host adds repo-specific build/test artifacts as globs in `<HERMES_HOME>/worktree-owner.json` `"disposable_ignored"` (matched per path component and per leading relative path, e.g. `["test_durations.json", ".hermes/bin"]`); a collapsed ignored dir counts only if every file under it matches. Symlinks committed to the repo (mode 120000) don't keep a tree either. Trees outside scratch with unsaved work remain for explicit owner cleanup or an independently verified backstop.
   **Deferred teardown (sweep).** The completion hook runs once, in the completing worker, and must decline while a
   child card is still open or a process still works in a tree; cards closed from the CLI/REST never run it at all.
   So after each completion the hook also starts, at most hourly, a detached `python3 __init__.py sweep`: for every
   marked tree on the host whose card is settled on its own board (done, latest run done/completed, or
   completed/completed for an operator or CLI close, at least 10 min and at most 8 days ago, no open child), it
   re-runs the same verified teardown under the board reservation. A tree
   kept for an open child logs `KEEP ... open child card(s) <ids>; torn down when they finish`. Every other sweep
   decline on a done card logs a KEEP line too and prints a `kept: <reason>` row (e.g. `latest run N is
   crashed/crashed`), so the GC report quotes today's reason, not the one from completion time. A marker written
   before markers carried `board_db`/`run_id` (legacy, looked up on `~/.hermes/kanban.db`) is never torn down by
   the plugin: it logs `KEEP ... legacy marker without a board/run id; mac-worktree-gc archives it after 7 idle days`
   and is left to the GC. The hourly
   `mac-worktree-gc.py --all-repos` also runs the sweep before its own pass (and `sweep --dry-run` under its
   `--dry-run`), so a host with few completions re-checks too; `sweep --dry-run` prints `would remove` rows and
   removes and logs nothing. Older trees belong to mac-worktree-gc. An untracked symlink named like a cache (`tests/node_modules` linked to a shared install,
   which a `node_modules/` ignore rule does not match) does not keep a tree either: git unlinks it, never follows it.
   A tree registered by an **earlier run of the same card** on the same board is torn down too (review and retry loops finish in a later run than the one that made the tree); markers from another card's run, another board, or legacy markers without a run id are kept. Every keep decision writes a `KEEP <path> (<owner>): <reason>` log line.

### Portability and safety limits

- macOS uses native `st_birthtime`; Linux uses GNU `stat -c %W`, falling back only to an intact initial HEAD reflog. Never ctime. Unknown creation times are not claimed.
- Linux scans `/proc` cwd links for the current UID. Exited processes and proven zombies are tolerated; unknown visibility fails closed. Non-dumpable same-UID services can deny readlink. Optional per-profile `<HERMES_HOME>/worktree-owner.json` with `{"linux_sudo_readlink": true}` enables only `sudo -n -- /usr/bin/readlink -- /proc/<pid>/cwd` fallback, with process-start identity checked before/after. It never prompts, changes sudoers, runs a privileged shell/Python, or ignores a failed probe. Without an existing noninteractive grant, cleanup stays conservative.
- macOS retains PATH-resolved `lsof` (fallback `/usr/sbin/lsof`); any warning, timeout, failed exit or empty scan prevents cleanup. Linux deliberately does not use Docker-sensitive lsof mount scans.
- **Core prerequisite:** Git-bearing scratch must be preserved inside `complete_task` before any post-tool hook. Fork commit `20358a17bdec17ed5692d9be0f4b99b60c3c4ddf` guards both direct and deferred-parent deletion, for tool, CLI and REST completion. Do not enable this lifecycle on an older core expecting it to protect nested scratch. CLI/REST completion preserves Git but does not invoke the tool-only callback; its trees are reaped by the next sweep (started by any later tool completion on the host, or the hourly mac-worktree-gc run).
- SSH-created remote trees, existing sessions and plain-shell work are not automatically owned. SSH commands are excluded from local attribution. For remote/manual work, run explicit `register_markers` on the owning host with its real owner environment and verified paths, or remove the worktree explicitly after safe archival. No directory glob confers ownership retroactively.
- Registration is best-effort, not a security boundary: command text and creation windows cannot prove arbitrary shell semantics or concurrent loop ownership. Unregistered trees remain protected by core preservation, not automatically reaped.
- Process and filesystem scans are snapshots, not locks on arbitrary external Git users. The board reservation serializes transitions on that board; it is not a cross-host filesystem lease. Never use this as a global managed-root exemption.

Log: `~/.hermes/logs/worktree-owner.log` (`REGISTER`, `REMOVED`, `KEEP`, `SKIP` lines).

## Claude Code and Codex (`agent_hook.py`)

The same rule for worktrees made by Claude Code or Codex sessions (and their subagents), as command hooks:

- `PreToolUse` / `PostToolUse` on Bash: a command that mentions `worktree add` gets the same `hermes-owner.json`
  marker, with `agent` (`claude` / `codex`), `agent_session_id`, `cwd` and, inside a Kanban card,
  `task_id` (so the card's `kanban_complete` teardown also applies). The pre-hook stamps the call's start time, so
  only gitdirs born during the call are claimed, with the same attribution guard. A `HERMES_KANBAN_TASK` paired
  with `HERMES_KANBAN_RUN_ID` is stamped only when that run still holds a live claim; a finished run is not a
  card. An unreadable board is left as-is. Repos searched: `~/code` depth
  1-2, `~/.hermes/hermes-agent`, the session cwd and any `cd <dir>` / `git -C <dir>` in the command.
- `Stop` (at most every 5 min per session) and `SessionEnd`: a detached child removes this session's marked trees
  that are clean, have no live process cwd inside, and are merged: HEAD is an ancestor of `origin/<default>` after a
  fetch, or a merged GitHub PR (`gh pr list --head <branch> --state merged`) has exactly HEAD as its head (squash
  merges, auto-deleted branches). The branch is kept. Unmerged, dirty or busy trees are left to the backstop GC.

`~/.claude/settings.json`:

```json
"PreToolUse":  [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 ~/.hermes/plugins/worktree-owner/agent_hook.py claude", "timeout": 10}]}],
"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 ~/.hermes/plugins/worktree-owner/agent_hook.py claude", "timeout": 30}]}],
"Stop":        [{"hooks": [{"type": "command", "command": "python3 ~/.hermes/plugins/worktree-owner/agent_hook.py claude", "timeout": 10}]}],
"SessionEnd":  [{"hooks": [{"type": "command", "command": "python3 ~/.hermes/plugins/worktree-owner/agent_hook.py claude", "timeout": 10}]}]
```

Codex: the same entries in `~/.codex/hooks.json` with `agent_hook.py codex` (proven with `codex exec`). Codex runs a
new hook only once it is trusted (it records a `trusted_hash` per hook in `config.toml`); until then only `codex exec
--dangerously-bypass-hook-trust` runs it.

Not covered: worktrees made by plain shells or other tools. Those need the backstop GC plus agent instructions
("remove the worktree you made once its branch is merged or abandoned").

## Install

```
ln -s ~/code/hermes-mods/plugins/worktree-owner ~/.hermes/plugins/worktree-owner
hermes plugins enable worktree-owner
```

New sessions and Kanban workers load it. Sessions that are already open keep running without it.

## Tests

```
python3 -B -m unittest discover -s plugins/worktree-owner/tests
```

Stdlib unit suite: 64 tests using disposable Git repos and boards, including actual archive recovery and staged-version preservation. Linux runs on Linux; macOS lsof semantics are exercised with subprocess fixtures, not claimed as a live macOS test.

Bootstrapped runtime integration (requires a core checkout with scratch preservation, and on Linux an existing noninteractive readlink grant for non-dumpable processes):

```
hermes --run-module cProfile -o /dev/null "$PWD/plugins/worktree-owner/tests/runtime_fixture.py" /absolute/path/to/patched/core
```

The fixture creates a disposable profile, discovers the actual plugin, selects its registered callback, uses real Kanban tool completion for clean/dirty/untracked/unpushed nested worktrees, verifies rejection preservation and post-success ordering, and performs a real Linux process scan without stubbing it. No production board transitions. This proves the candidate code; deployment must separately verify the default runtime contains the prerequisite core commit.
