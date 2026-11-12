# Priority migration tolerates churn and coordinates all origins

Patches: **2512–2513**.

## Behavior

The [global priority migration](kanban-priority-bounds-and-global-migration-2409b.md) needed a quiet window across every origin and was a hand-run sequence of per-origin commands. Now:

- `hermes kanban --board <b> supervisor-requests --priority-operation migrate --writer <origin> --host-map-file <file> [--exception ...] [--allow-legacy-rollback]` (or `--host-map '{...}'`, exactly one) coordinates every origin: prepare locally from the canonical Board snapshot, stage the exact manifest bytes on each remote origin over SSH stdin, validate all staging receipts before any apply, apply each origin, publish each outbox, then run the canonical read-only verify. The host map names, per origin, the SSH host, command argv prefix, an existing local task and a fresh absolute backup path.
- New operations `publish` (outbox push without a snapshot pull) and `migrate`; `stage --manifest -` reads stdin.
- Churn during the migration no longer forces a new manifest: cards that went `done`/`archived` since capture are skipped and reported, new open cards owned by the writer at or below the priority cap are reported as arrivals; ownership drift, a missing planned row, or a new open card above the cap or without an origin still fail. No quiet window or dispatcher freeze is required.
- Any apply failure (refusal, SSH timeout, invalid receipt, or any exception from the in-process local writer such as a locked database) stops further applies and attempts guarded restore on every attempted origin in reverse order; a restore that raises is recorded and the rest still run. The command exits nonzero and prints the full JSON report (`attempted`, `applied`, `restored`, `published`, `errors`).

## How it works

`hermes_cli/kanban_priority_runner.py` (`load_hosts`, `ssh`, `run`) is the coordinator; `kanban_priority_cli.py` dispatches via a handler table (`_prepare_stage`, `_apply`, `_publish`, `_verify`). `kanban_priority_inventory.classify` separates active rows from skipped and arrived ones; `kanban_priority_executor.apply_attached` and `kanban_priority_prepare.verify` use it and add `skipped`/`arrivals` to their receipts. The operator fence (`_non_worker_refusal`) runs before reading the host map, before spawning SSH and, on remote staging, before reading stdin. Patch 2513 widens the three guarded-restore `except` clauses in `run` to `Exception` and logs each. Rollout steps live in `docs/kanban-priority-rollout.md`.

## Code

`hermes_cli/kanban_parser.py`, `hermes_cli/kanban_priority_cli.py`, `hermes_cli/kanban_priority_executor.py`, `hermes_cli/kanban_priority_inventory.py` (new), `hermes_cli/kanban_priority_prepare.py`, `hermes_cli/kanban_priority_runner.py` (new), `docs/kanban-priority-rollout.md`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_priority_churn.py` (departures, arrivals, ownership and value fences, canonical churn both directions, via `classify`/executor/verify), `tests/hermes_cli/test_kanban_priority_runner.py` (the `migrate` coordinator through the CLI wiring with a fake and a real-subprocess fake SSH: staging before apply, digest mismatch, restore after refusal or unacknowledged apply, lineage fence before stdin/SSH, host-map coverage, `publish`, rollback verification; for 2513 a local apply or restore exception, 4 cases), and updates to the executor and verify tests. Live SSH to real origins is untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Every origin in the captured manifest needs a host-map entry, and backup paths are consumed even when a later step fails; pick new paths to retry.
- `migrate` rejects `--task`, `--backup`, `--manifest`, `--attachment-id` and `--digest`; those come from the map.
- Publication or canonical-verify failure exits nonzero without automatic rollback; only apply failures trigger restore.
- Operator-only: workers and delegated lineage are refused.

## Plugin

Core change; no plugin required.
