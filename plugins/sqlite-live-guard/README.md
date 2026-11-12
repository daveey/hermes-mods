# sqlite-live-guard

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
core's exact generated-index keys to differ; known FTS internal tables may be rebuilt.
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
