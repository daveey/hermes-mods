# portable_reason audit and fenced placement evidence

Patch **2093**.

## Behavior

- When worker pools are configured, every card create (CLI and `kanban_create`) writes `portable_reason` (a reason string or null) into its `created` event. With no pool configured nothing is added.
- Producers may fence quoted incident evidence with `<!-- placement:evidence -->` ... `<!-- /placement:evidence -->`. Only complete pairs are removed before the host-need scan; an unterminated marker exempts nothing and the title is never fenced. Evidence no longer pins a repo-fix card to the machine named in the incident text.

## How it works

`kanban_worker_placement.creation_audit` (never raises) calls `kanban_placement.portable_reason`; `placement_audit` threads through `create_task` into `record_created`. `without_evidence` strips fenced blocks inside `portable_reason`.

## Code

- `hermes_cli/kanban_placement.py`, `hermes_cli/kanban_worker_placement.py`, `hermes_cli/kanban_db_creation.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban.py`, `tools/kanban_tools.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/hermes_cli/test_kanban_portable_reason_audit.py`: marker constants; fenced evidence is not a host need; unfenced evidence still keeps the card home; the fence never hides work text; CLI and tool created events record the reason; no audit without a pool; `create_task` stores a passed reason.

## Applying

Apply ordinal(s) 2093 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Depends on the worker-placement series ([kanban-worker-requeue-and-crash-records.md](kanban-worker-requeue-and-crash-records.md) and neighbours). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

The audit is advisory; failures log a warning and never block a create. Markers must match exactly.

## Could this be a plugin?

No. It extends core task creation and placement.
