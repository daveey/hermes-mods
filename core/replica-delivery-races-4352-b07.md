# Replica notice and replay race regressions

Patches: **4270, 4281, 4294–4295, 4299, 4306, 4318, 4337** (4352 snapshot).

## Behavior

Retrying a wall replay updates only a plan that still exists, so another owner's deletion is not resurrected.

Shipped regressions cover remembered-publication eligibility, shutdown destination custody, replica replacement, frozen withdrawal recipients, failed bystander ledger writes and late-load sequence races.

## How it works

The replay retry uses UPDATE rather than recreating a missing state_meta key. The remaining entries in this group are test-only: independent eligibility controls, held-destination marking, same-sequence replica replacement, frozen-recipient first-seen persistence across restart, rollback retry with unchanged replica inputs and sequence validation before claim settlement. A same-process lease handoff fixture waits for gateway metadata publication.

## Code

- `gateway/session_wall_replay.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/gateway/test_session_wall_replay_retry.py`
- `tests/gateway/test_shutdown_route_order.py`
- `tests/hermes_cli/test_kanban_replica_bystander_memory.py`
- `tests/tui_gateway/test_kanban_remote_index.py`
- `tests/tui_gateway/test_remote_projection_bystander_memory.py`
- `tests/tui_gateway/test_remote_projection_withdrawal.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Do not interpret these test-only commits as new delivery APIs. An unchanged sequence alone does not establish unchanged file identity, and a failed ledger transaction must remain retryable. Runtime fixes outside the replay UPDATE predate these test additions.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
