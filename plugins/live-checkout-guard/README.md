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

## The live core checkout

The live Hermes core checkout is protected too: `~/.hermes/hermes-agent` and the main clone the running
`hermes_cli` was imported from (the mini runs core from `~/code/hermes-agent`); `$LIVE_CORE_CHECKOUT`
(`:`-separated) replaces both, and `LIVE_CORE_CHECKOUT=0` turns this part off. Only git checkouts count, and the
in-process one only when `.git` is a directory, so a backend started from a linked worktree never locks it.
Every running backend imports from that tree and the core updater only fast-forwards it while it is clean and on
`main`, so the rules there are stricter than for hermes-mods:

- Every HEAD move or tree write is refused: `checkout` (incl. `--detach`), `switch`, `reset`, `merge` (also a
  clean `--ff-only`), `pull`, `rebase`, `commit`, `stash` (not `list|show`), `cherry-pick`, `restore`, `apply`, `am`,
  `revert`, `clean`, `update-ref HEAD|refs/heads/*|--stdin`, by cwd, `cd`, `git -C`, `--git-dir`/`GIT_DIR`.
- Reads stay allowed: `show origin/main:<path>`, `log`, `diff`, `status`, `grep`, `fetch`, `worktree add`
  (outside the tree), `merge-base`.
- File writes are refused like in hermes-mods, except gitignored runtime paths (logs, venv, build output), the
  same exemption as coach's rule; `node_modules` stays refused (it is the live dependency install).
- One-command escape hatch for a git step the user asked for (e.g. putting a detached checkout back on main):
  `COACH_ALLOW_LIVE=1 git -C ~/.hermes/hermes-agent checkout main`, the same hatch coach honours.

Why here and not only in coach: coach's `blocks_live_checkout` had this rule, but worker nodes run coach
executor-only (not in `plugins.enabled`), while this guard is enabled on every host and profile. On 2026-11-12 a
worker agent ran `cd ~/.hermes/hermes-agent && git checkout -q --detach 2>/dev/null; git show origin/main:… | sed …`
there and the updater skipped every pass for 1h25m. Tests: `tests_core_root.py`.

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
- `skill_manage` remains allowed by the pre-call guard, except an **open-PR overlap**: a mutating op
  (edit/patch/write_file/remove_file/delete, flat or batched) on an existing skill inside a GitHub-backed clone is
  checked against the open PRs into that clone's upstream branch (`pr_overlap.py`: repo from `remote.origin.url`,
  base from `@{u}`, path relative to the clone). A file in an open PR's file list is refused with the PR number and
  URL, and the writer is told to send the note to the PR's owner instead (editing the shared copy changes the PR's
  base, forcing a reconciliation and another exact-head review). Only a confirmed overlap blocks: an unavailable or
  incomplete lookup (HTTP error, rate limit, 8 s deadline, truncated pagination) allows the edit, and the skills
  publisher (state `hosts/hermes-hub/skills-sync/skills_sync.sh`, the same `pr_overlap.py check` CLI) holds it
  uncommitted instead and retries. Reads are cached in `~/.hermes/cache/pr-overlap/` (PR list 120 s, files per head
  sha; files are read from each PR's canonical base repo, so a moved repo's 301 does not double them). Transport:
  the hook reads only through the host's `gh` (`gh api --include`, one call per request, up to 4 file reads at a
  time), never with a raw token over urllib, so a metering `gh` wrapper (a GitHub quota ledger) sees every read;
  `PR_OVERLAP_GH` overrides the binary, and gh missing or refusing (e.g. a quota guard's exit 75) is unknown. The CLI
  keeps urllib with the caller's token (`GH_TOKEN`/`GITHUB_TOKEN`, else `gh auth token`). Escape hatch for a user-approved edit:
  `SKILL_PR_OVERLAP_GUARD=0`. Tests: `tests_pr_overlap.py`. Its successful documentation writes are
  published by the post-call observer described below; failures never turn a successful tool result into an error.
- Claude Code: `claude_hook.py` is a PreToolUse hook for `Bash` and `Write|Edit|MultiEdit|NotebookEdit`.
  Install: `python3 plugins/live-checkout-guard/claude_hook.py --install [--settings PATH]` (idempotent; default
  `~/.claude/settings.json`). Exit 2 blocks; bad input fails open.

## Bounded skill documentation publication

`post_tool_call(tool_name, args, result, task_id, session_id, status, **kwargs)` observes
successful `skill_manage` / `mcp__skill_manage` calls. Flat create/edit results carry a directory
`path`, supporting writes carry a file `path`, flat patches have no path, and successful atomic
batches return compact `results` rows (`name`, `action`, `file_path`, `success`) without paths.
For the latter cases the helper uses core's `_find_skill` and its configured lookup precedence,
not its own basename search. Failed/rolled-back batches and approval-staged results are ignored.
Only create/edit/patch/write_file are eligible; delete/remove_file are deliberately not published.
Repeated paths in a batch are deduplicated and their final on-disk snapshot is published once.

`skill_publish.py` permits `skills/.../SKILL.md` and non-executable supporting files below
`references/`, `templates/`, or `assets/` with these explicit extensions:
`.md .txt .rst .csv .json .yaml .yml .png .jpg .jpeg .gif .webp .pdf`.
Scripts, plugins, other code extensions, dot components, traversal, symlinks within the live
root, non-regular files, executable modes, and shebang content are refused. External aliases
into the root are supported without hiding internal symlinks. This is a conservative file-scope
policy, not a content-classification sandbox (code examples inside documentation are normal).
Maximum: 16 selected files, 1 MiB per file. A mixed unsafe/safe selected batch is refused whole.
Private skills outside the root are ignored; unrelated live dirt is neither staged nor published.

Publication fetches origin/main, then queries `gh run list` for push runs at that exact tip.
Every latest workflow run must be completed/successful. Red, missing, malformed,
unreadable, or truncated CI evidence skips publication with one log record; unknown fails closed.
The observer never waits for CI. A tip whose runs are only still queued/in progress (`pending`), or a
busy publication lock, is **deferred** instead of dropped: a record goes to
`$HERMES_HOME/cache/skill-publish-pending/<key>.json` (root, paths, session/card, 30 min window) and a
detached `skill_publish.py --retry-pending` worker (one per home, flock) retries every 20 s. Each retry
is a full `publish()` from a fresh snapshot: the tip's CI must be green, and every scope, race,
gitleaks and reconcile rule below applies unchanged. Red CI or any publication error drops the
record (edits stay in place, as for a direct skip); a still-pending or unknown tip keeps it until
the window ends. Log: `$HERMES_HOME/logs/skill-publish-retry.log`. A later `skill_manage` call
restarts the worker if records exist but none is running. A clean detached worktree starts at fetched origin/main and receives a
`git apply --3way --index` patch constructed from captured bytes using a separate temporary index.
Commits use the inherited repository author identity, explicitly select `.githooks`, and include
skill/session/card in their subject. Cross-skill batches use separate scope-checked commits in
the same detached worktree. Privacy/scope bypass environment flags are stripped for commits.
A non-fast-forward push may fetch/rebase/retry at most **three** times (four pushes total),
rechecking CI at every new tip. A refusal without a changed tip stops immediately. No force push.

Archives persist under `$HERMES_HOME/kanban/evidence/mods-reconcile/` (default `~/.hermes`)
as `skill-publish-*` with `manifest.json`,
`changes.patch`, `untracked.tar.gz`, and `publish.patch`. After remote ancestry readback,
the helper invokes the existing `reconcile.py` with the exact archive and selected paths.
It restores only unchanged archived leftovers to live HEAD bytes, leaving live HEAD and index
untouched for a **later** clean `merge --ff-only origin/main`; the observer never deploys itself.
Detached worktrees are removed on success or failure; archives remain for recovery. Errors
leave source edits untouched or recoverable in that archive. No stash/reset/commit in live.
A later filesystem failure can leave a partially reconciled multi-file selection; its original
bytes are still archived; completed reconciliations append an audit record.

An exclusive per-checkout publisher lock prevents overlapping helpers; selected staged changes
are refused. Captured file identities/bytes and live HEAD are checked again after publication;
`reconcile.py` performs its own last-moment checks and cooperating-helper lock. Observed concurrent
writers are never overwritten. As with reconciliation itself, arbitrary noncooperating writes
cannot be portably excluded in the final check/replace window. This is not an OS lock on skills.
Git subprocesses have 30-second limits, external CI has 20 seconds, reconciliation has 90 seconds;
there is no observer retry loop beyond the bounded push race retries. The guard kill switch
also disables this observer. Sync fallback is separate and not implemented here.

Tests: `tests_skill_publish.py` uses real bare origins, real `.githooks`/gitleaks/fixture denylist,
and only injects the external CI service. It covers publication/clean source/later fast-forward,
inherited identity, selected multi-file and cross-skill batches, unrelated dirt, scope and alias
rejections, red/unknown CI one-log skips, in-progress-then-green deferred publication through the
real hook and detached worker (no second `skill_manage` call), pending-then-red drop and window
expiry, actual pre-push races and retry exhaustion, real hook
refusal, staged changes, and a concurrent writer. `_skill_publish_probe.py` exercises real-core
resolution of compact batch rows (skipped when core is absent); it does not mutate core.
Run: `python3 -m unittest discover -s plugins/live-checkout-guard -p tests_skill_publish.py -v`.
The real publication fixtures require gitleaks; without it they skip, never bypass privacy hooks.

## Throwaway HERMES_HOME in the terminal session

The Hermes `terminal` tool re-sources its `export -p` snapshot before every call, so
`export HERMES_HOME=$(mktemp -d)` in one call is still set in every later call of that session. On 2026-11-02 a
coach test recipe did exactly that, and the same session's next `hermes-sync run --no-core` exported the
throwaway home into the private state repo and re-pointed the real `com.daveey.hermes-sync` LaunchAgent at it.

The guard refuses a `terminal` call (direct, `mcp__terminal`, batched, or a literal `terminal()` in
`execute_code`) whose top-level code exports `HERMES_HOME` to a temp path: `mktemp`, `$TMPDIR`/`$TMP`, `pytest`
basetemps, `/tmp`, `/var/tmp`, `/var/folders` and `/private/...`. That covers `export`/`declare -x`/`typeset -x`
and an assignment-only `HERMES_HOME=<temp>` statement (which keeps an inherited export exported). Still allowed: the
one-command prefix form `HERMES_HOME=<temp> cmd`, `env HERMES_HOME=...`, subshells `( export ...; cmd )`,
`bash -c '...'`, `$(...)`, heredoc bodies, quoted text, comments, and any non-temp value such as a profile home.
`process` stdin and execute_code's own `subprocess` shells are not checked: they end with their process.
hermes-sync itself also refuses host-global passes from a non-canonical home (state repo, `hermes-sync`).
Tests: `tests_home_export.py` (incl. a two-call run through the real core terminal tool).

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
version supporting prepared `update-ref` transactions (tested on Git 2.55 and 2.43
on Linux, and the full helper test suite on macOS with Apple Git 2.54 and
`/usr/bin/python3` 3.9.6). Git older than 2.45 lacks `rev-parse --show-ref-format`
and has no reftable backend, so there the backend is read from
`extensions.refStorage` (unset means files). Only the files ref backend
is supported; shallow repositories
are refused. The `live-checkout-guard` GitHub workflow runs the helper suites on
Python 3.9 and 3.14.

The existing archive directory must be **outside** the checkout and Git directory.
Its `manifest.json` is a JSON list with one row for **every** current stash:
`{"stash":"FULL_OID","patch":"relative.patch","bytes":123,"sha256":"SHA256"}`.
An absolute `patch` must name a file inside the archive; only the archive's parent
may be spelled through a symlink (macOS `/var` or `/tmp`). A symlink at or inside
the archive is refused in every spelling.
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
killed), stderr retention, stderr in a file (a late write after the CLI gives up
cannot SIGPIPE Git) and no use of `Popen.communicate` (on Python 3.9 it
flushed the already-closed stdin after a committed deletion, then failed again on
retry); `SymlinkedArchiveParentTests` covers absolute rows in both spellings and
symlinks inside the archive. `tests/test_retirement_cli.py` exercises all three CLI modes on the running
interpreter and on Python 3.9 (`HERMES_MODS_PY39` or `python3.9`; skipped locally
when absent, a failure under `CI`), the pre-mutation old-interpreter refusal, plus
terminal/execute-code guard checks. Existing
`tests_reconcile.py` retains the separate file-reconciliation constraints.

## Limitations (best-effort, not a sandbox)

- Lexical analysis only. `$NAME`/`${NAME}` in shell paths (and `cd` targets) expand from assignments earlier in the
  same command (`D=$TMPDIR/x; rm -rf $D`) and then the guard process's environment; a path that stays unresolved
  (unset variable, `$(…)`, backticks, `${X:-…}`) is not proven inside and is allowed, and after `cd` to such a path
  later relative paths are too. Globs, `xargs`, `find -exec`,
  exported `GIT_DIR` (set in an earlier segment/call), aliases, functions, scripts that write on their own (`make`, `npm`,
  editors, `python script.py`) are not seen.
- `execute_code` with non-literal paths/commands (built strings, loop variables, imports of other modules) is not inspected;
  only literal arguments are. A tool called by name through another route (e.g. an MCP tool that writes files) is not covered.
- Subshell/`cd` scoping is flattened: `(cd root; …)` leaves later segments thinking cwd is root (can over-block);
  checkout-write checks scan heredoc bodies as if they were commands (can over-block text containing
  `git commit` while cwd is root), each body on its own: an apostrophe in a body or a `#` comment (`it's`) no longer
  hides the commands after it, and the `$(...)`/backticks of an unquoted-delimiter body are scanned as the commands
  they are. A line whose quotes never close is judged by its whitespace words; the lines around it are still parsed.
  The separate update rule skips data heredocs. It remains lexical:
  unusual shell grammars, arbitrary scripts and dynamic commands are not a security boundary.
- Wrapper option tables cover the common wrappers above; an unknown wrapper or option that takes a value can still hide
  the real command.
- The session cwd is read only from a core already loaded in the hook's process (it always is inside Hermes);
  elsewhere the hook falls back to `$TERMINAL_CWD`/process cwd.
- Tests: `scripts/test-all -k live-checkout-guard`, or `python -m pytest -q -p no:cacheprovider tests_guard.py` here.
  `test_real_core_registered_hook_uses_persisted_terminal_cwd` runs `_core_probe.py` under a Hermes runtime python
  (`$HERMES_CORE_PYTHON`, else `~/.hermes/installs/*/environments/*/venv/bin/python`) against `~/.hermes/hermes-agent`
  (`$HERMES_AGENT_DIR`); it skips when none can import the core.

## Decision-module updates in running workers

Once this reload boundary has been activated, running workers pick up changes to
`guard.py` on their next tool call, without exiting. The hook stats its nanosecond
mtime and size before computing a verdict; unchanged code is not re-read. A changed
file is executed into a fresh module, then swapped only after successful loading.
A syntax/load error retains the last good decisions and logs one warning per changed
fingerprint. Successful reloads log `live-checkout-guard: reloaded guard.py`.
`__init__.py`, hook registration and tool schemas are **not** hot-reloaded by this
boundary; their existing activation paths still apply.

Regression coverage: `tests/test_guard_decision_reload.py` at the repository root
loads temporary plugin copies and exercises the registered pre-tool hook across
allowed-to-refused edits, syntax errors, recovery and same-second/same-size edits.
