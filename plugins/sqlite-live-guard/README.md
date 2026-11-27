# sqlite-live-guard

## Shell execution-context checks

The existing shell policy is applied first. A second, bounded lexical pass follows
literal `ssh` remote command arguments (including OpenSSH options), explicit
`sh`/`bash`/`dash`/`zsh`/`ksh -c` scripts and shell-owned heredoc input. It resolves
simple ordered `NAME=value`, `$NAME` and `${NAME}` path aliases without treating
expanded values as new shell syntax. SSH does not inherit local shell aliases;
local shell children receive exported and command-prefix variables. Quoted data
arguments to commands such as `echo` are not interpreted as scripts.

The pass checks file operations and redirects against the existing live-file and
quarantine predicates. Conditional assignments retain possible values; an
unconditional assignment resets them. Subshell, pipeline and background assignment
scopes do not replace the parent's aliases. `readonly` bindings survive failed
reassignment/unset. Local child shells inherit the analyzed working directory; local relative paths
resolve against the supplied cwd and literal/alias `cd` targets. Conditional `cd`
retains both possible directories (up to 32, then refusal), including across nested
shells. Remote initial cwd remains unknown: explicit home/absolute paths or a
lexically known `cd` are required; local filesystem state never resolves it.
`mv -t DIR`, `-tDIR` and `--target-directory[=]DIR` protect every source subtree.
Prefix assignments enter the child environment without changing the invoking
shell's word expansion. Redirect operators are split before variable expansion,
including `>path`, `1>path` and `>|path`; quoted/escaped operators remain data.
Local directory targets retain the existing `_dir_like` policy; remote directory
targets require explicit directory syntax or a recognized home root, never a local
stat. Nesting and possible-value expansion are bounded, with one aggregate work
budget shared by all variants and nested scripts; exceeding a bound or failing to
load the analyzer refuses execution. The aggregate budget is 32768 units: it counts
source characters (including data/heredocs), expanded characters **before** result
allocation, and per-statement/variant work. This deliberately fails closed on large
benign commands too; it is not a maximum accepted input size. Nesting is limited to
eight child boundaries and possible-value products to 256. Remote analysis performs
no local SQLite version probe, directory stat or quarantine-directory inspection.
A detected raw replacement is refused in favor of the existing `swap_db.py`
preservation/ownership policy; this pass never installs a database itself.

This is defense in depth, **not a shell sandbox or a complete interpreter**.
Unsupported forms include dynamic commands (`eval`, functions, sourced scripts,
command substitution/backticks), computed/default-value parameter expansions,
ANSI-C quoting, unquoted word-splitting/globbing, SSH wrappers, stdin pipelines or
here-strings into interpreters, and remote relative working-directory inference.
A remote `sqlite3` CLI (bare or a path ending in `/sqlite3`, reached through `ssh`,
a remote `bash -c`/`-lc` or a shell reading an ssh heredoc) that opens a live Hermes
database on that host (`~/.hermes/...`, `$HOME/.hermes/...`, `/home/<user>/.hermes/...`,
`$HERMES_HOME/...`, outside the not-live cache/archive/backup/tmp paths) is refused,
`-readonly` included: the remote SQLite version cannot be checked from here, so it is
assumed to have the WAL-reset bug. The refusal names
`~/.hermes/bin/hermes-core-python -m sqlite3` on the remote host, which is not refused.
When ssh splits a remote `bash -lc 'script'` into several words (ssh joins its
arguments with spaces, so the remote script is only the first word), the joined words
are judged as the intended script as well (fail closed).
There is no remote write/maintenance SQL policy, no remote `python -c` detection and
no remote version discovery.
Use explicit absolute paths and the guarded helper, not alternate syntax to evade
checks. A passing lexical check is not authorization for a database replacement.

Regression coverage is in `tests_guard.py`: remote rollback-shaped swaps, option
boundaries, alias ordering/branches/scopes, heredoc ownership and real
`claude_hook.py` subprocess allow/refuse controls. All inputs are synthetic; tests
do not execute SSH or modify a production database.

## Offline recovered state installation (Linux)

`swap_db.py` is an **offline operator command**, not an automatic recovery daemon.
Run from a separate shell, never a session served by the profile being recovered.
Use the installed Hermes core Python environment (with core on `PYTHONPATH`):

```sh
python3 swap_db.py --db /offline/profile/state.db \
  --replacement /offline/recovered.db --recovered-state --ack-runtime-loss
```

First drain and stop **all** writers and readers, including timers/socket activation
and remote clients. Keep them disabled for the entire maintenance interval.
`--stop-unit UNIT` may be repeated for systemd **user** units; only units this command
successfully stopped are started after verified installation. There is no kill-all,
ignore-holder, automatic rollback, or restart-on-abort option. A start failure after
verification may leave some units started; consult `started_units` in the receipt.
A process crash cannot execute a finally block: operators must keep automatic
activation disabled until they reconcile the receipt. The canonical main file stays
present through an atomic replacement; sidecar transitions still require offline
ownership. The pending marker is a retry fence for this helper, not a core boot gate.

### Loss policy

All `state.db` installations require `--recovered-state`; plain mode cannot bypass
epoch/idempotency checks. The exact omission allowlist follows
`hermes_cli.session_recovery._RUNTIME_TABLES`:

- `runtime_epoch` (its readable monotonic floor is staged, not reset);
- `session_admissions`, `worker_executions`, `worker_receipts` **only when empty**.

The explicit acknowledgement accepts only the reported allowed runtime omissions.
It never authorizes loss of accepted-work/idempotency records, grants, revocations,
unknown tables, or durable rows. There is no `hosted_room_*` wildcard. A populated
runtime ledger excluded by core recovery is a refusal, not an invitation to reset it.
Table presence is insufficient: recreated-empty tables are detected by counts and
changed durable/security rows by exact row-hash multisets. `state_meta` permits only
core's exact generated-index keys to differ; known FTS internal tables may be rebuilt, and
the derived `session_change`/`session_change_revision`,
`session_content_change`/`session_content_revision` and
`throughput_revision`/`throughput_anchors`/`throughput_changes` cursors may be rebuilt.
Recovery creates fresh content/throughput epochs/nonces and regenerates journals from
canonical sessions, messages and usage. Readers reject stale epochs (and throughput anchor
history) and reload full contributions. This exception applies only in recovered-state mode
and to these exact names; `session_model_usage` and unknown `session_content_*` or
`throughput_*` tables remain durable.
Epoch and AUTOINCREMENT sequence floors cannot decrease. Schema evolution, unreadable
source tables and partial salvage with lost durable rows conservatively refuse.
This helper is not a universal installer for `--allow-partial` outputs.

The helper inventories a **private copy of the stopped source bundle**, including
its WAL. If source inventory cannot be read, obtain a reviewed preservation plan;
do not bypass the check with an empty source. It snapshots the replacement using
SQLite backup (including committed WAL), validates staged integrity and foreign
keys, and verifies installed bytes. A 60-second replacement-backup bound prevents
an indefinitely busy input from hanging the command. The original and input remain
available for forensic review; replacement file permissions are narrowed to 0600.

### Ownership and holders, including Desktop SSH serve

The recovery flock serializes helpers. State installs also acquire core
`gateway.runtime_ownership.exclusive_maintenance`, preventing coordinated gateway
startup while installing. Missing core is a supported refusal, not a weaker fallback.
FD and mmap scans inspect target and archived inodes plus active/deleted sidecars,
before and after publication. PID and process-birth identifiers are diagnostic
identities, **not authorization to send signals**.

If inspection is permission-denied, `--holder-scan-sudo` requests `sudo -n` for the
read-only scanner only. It does not run the installer or service actions as root.
No password prompt is attempted. Restricted `/proc` mounts/nested PID namespaces
refuse; run from the host with visibility of every opener. Network/shared mounts
and other hosts' writers are not covered: do not use this local helper there.

For an SSH session-scope `serve --isolated` holder:

1. Record the reported PID, birth, path and command. Identify the **exact** Desktop
   connection/profile owning it. A service stop does not cover session-scope serves.
2. Use the installation's supported `hermes-sync pause-turns -- <bounded supervisor
   action>` where available to drain active turns. Pausing is NOT database closure.
3. Close that Desktop SSH connection through its owning client, or stop only its
   identified supervisor using the supported control surface. If no bounded shutdown
   is available, leave the install refused and obtain operator help for that owner.
4. Re-run the helper and require zero holders. Do not kill unrelated SSH shells,
   use broad `pkill`, or use `hermes serve --stop` (it can stop every serve process).

### Receipts and partial cutover

Each attempt gets a private adjacent `.state.db.recovery-<unique>/` directory with
`receipt.json`, staged/source files and `original.db` plus preserved sidecars.
Receipts are atomically written/fsynced at phase boundaries and before each move;
source/stage/installed fingerprints, row counts, epoch/sequence floors, next move,
completed moves and service outcomes are retained. No transcript rows are logged.

`state.db.swap-pending.json` is fsynced **before** the first archive operation.
A crash or failed rename leaves it in place; retries refuse. Keep every producer
stopped, inspect its referenced receipt, verify actual paths/inodes (a crash can
occur between a rename and its completion receipt), and validate a *copied complete
bundle* before deciding how to resume. Never start onto an original known corrupt,
remove the pending marker just to retry, or mix a new main with old sidecars.
Only an operator's reconciled, validated bundle may clear that marker. No automatic
partial rollback is attempted. Successful install keeps the original archive.

### Regression coverage

```sh
PYTHONPATH=/path/to/hermes-core SWAP_TEST_PRIVILEGED_SCAN=1 \
  python3 -m unittest discover -s plugins/sqlite-live-guard -p tests_swap.py -v
python3 scripts/test-all -k sqlite-live-guard
```

The optional privileged test flag only enables the read-only scan on restricted
Linux hosts. Tests use disposable databases, fixture subprocess holders and a fake
systemctl executable; none stop real units. Cases cover allowed/unknown/durable
omissions, empty recreated security tables, populated idempotency loss, epoch and
sequence floors, FK failures, committed replacement WAL, SSH/deleted/mmap holders,
late holders surviving publication, single-owner/maintenance refusal, abort/partial
cutover, and actual `hermes sessions recover` → helper installation. Core-only E2E
skips explicitly when core is absent; that skip is not proof of integration.

## Source-review hardening

Local file operands are resolved (including symlinks and `..`) **before** testing
live-file names or cache/backup exclusions. Remote operands use lexical `..`
normalization only; remote decisions never resolve this host's symlinks. Recursive
`rm` and source-directory `mv` also protect Hermes-home ancestors and potentially
live subdirectories, conservatively including empty live subtrees. Cache/archive/
backup trees retain their exclusions after normalization. For remote paths,
database/backup-shaped operands are treated as files because local directory
metadata cannot establish their remote type; remote directory targets should use
explicit directory syntax. This remains defense in depth, not confinement against
filesystem changes between classification and execution.

Python AST checks resolve `from os import unlink/remove` (including `as` names)
and `import os as ...` identities, literal and simple assigned targets, and the
`path=` deletion argument. They do not execute submitted code or implement arbitrary
Python dataflow, dynamic imports or rebinding.

Recovery PID, receipt and pending-marker writes use adjacent unique exclusive
0600 temporary files, write/fsync through the opened descriptor, and atomically
replace the destination. Pre-placed predictable `.tmp` symlinks are never opened;
failed writes/publications clean up their unique temp. The offline ownership and
holder-scan requirements above remain unchanged.

Regression coverage: `tests/test_source_review.py` (D1 path/ancestor/symlink,
D2 import aliases and scratch controls, D3 symlink victim/mode preservation,
unique temps and failed-publication cleanup).

## Decision-module updates in running workers

Once this reload boundary has been activated, running workers pick up changes to
`guard.py` on their next tool call, without exiting. The hook stats its nanosecond
mtime and size before computing a verdict; unchanged code is not re-read. A changed
file is executed into a fresh module, then swapped only after successful loading.
A syntax/load error retains the last good decisions and logs one warning per changed
fingerprint. Successful reloads log `sqlite-live-guard: reloaded guard.py`.
`__init__.py`, hook registration and tool schemas are **not** hot-reloaded by this
boundary; their existing activation paths still apply.

Regression coverage: `tests/test_guard_decision_reload.py` at the repository root
loads temporary plugin copies and exercises the registered pre-tool hook across
allowed-to-refused edits, syntax errors, recovery and same-second/same-size edits.

## SQLite build check (2026-11-18)

Hermes holds its databases with SQLite 3.53.1 (the managed Python). SQLite 3.7.0-3.51.2 has the WAL-reset bug
(https://sqlite.org/wal.html#walresetbug): with two connections on one WAL database, a write racing a checkpoint can
corrupt it. Ubuntu 24.04's `/usr/bin/sqlite3` and `python3` link 3.45.1, macOS's 3.51.0. On 2026-11-18 the hub's
`state.db` search index was found with pages referenced twice while such a process held it.

So any command that would open a live database (reads and `-readonly` included) with a `sqlite3` binary,
`python -c` or `python -m sqlite3` whose SQLite is affected is refused. The program's SQLite is asked by running
it (`sqlite3 --version`, or the interpreter's `sqlite3.sqlite_version`) only when it is a root-owned, non-writable
binary in a system directory, with an empty environment, `cwd=/`, a 5 s limit and its process group killed on
timeout; a bare name is looked up on the system directories only. Anything else (a relative path, a binary in a
user directory, no answer) is unknown and not blocked: the guard never runs a program an agent wrote. Answers are
remembered per binary within one process (each Claude Code hook call is a new process). The refusal names
`~/.hermes/bin/hermes-core-python -m sqlite3 <db> "<sql>"` (managed SQLite), which the write and maintenance rules
above judge like `sqlite3`; a raw Kanban write gets the board refusal first. Not inspected: script files run by an
affected `python3`, `bash -c`/`xargs` wrappers, `uv run`, and Python heredocs.
