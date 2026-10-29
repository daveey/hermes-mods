# Cross-process update admission fence

Patch **0503** (source `dc5c245525`, squash of the PR #98 series).

## Behavior

- `hermes update` can hold admission across processes: while a hold exists, new Desktop prompt reservations and Kanban
  claims are refused without mutation, and an existing reservation survives.
- Desktop pending prompts and workers are counted by the fence; a busy admission defers without waiting or leaking a
  writer; a lock timeout or a crashed holder releases the lock.
- Kanban claims reject a nested transaction before waiting, and the claim fence spans commit while hooks run after
  release. The retirement idle probe can read the fence without deadlock.
- Turn handoffs are retained and stale handoffs are isolated; queued prompts are retried; notification claims adopt
  their own turn generation, and settling clears it so a later direct turn owns the session.

## How it works

`hermes_cli/admission_fence.py` is a stdlib-only cross-process lock/reservation file (usable standalone);
`hermes_cli/kanban_admission.py` and `kanban_db.py` wrap claims; `hermes_cli/backend_retirement.py` composes with it;
`tui_gateway/admission_retry.py` and the prompt dispatch paths take reservations.

## Code

`hermes_cli/{admission_fence,backend_retirement,kanban_admission,kanban_db}.py`, `tui_gateway/{admission_retry,compute_host_bridge}.py`
and the prompt dispatch modules.

## Tests

`tests/hermes_cli/test_admission_fence.py`, `test_kanban_admission_contention.py`,
`tests/tui_gateway/test_update_admission_{caller,generation,handoff,retry}.py`, `test_bot_live_owner_delivery.py`,
`test_ephemeral_profile_override.py`. The end-to-end `hermes update` cutover on a real host is untested here.

## Applying

`git am -3 core/patches/0503-*.patch`. It touches `kanban_db.py`; later kanban patches assume it.

## Gotchas

The fence is a snapshot gate at admission time, not a proof that no turn started elsewhere; see
[core-update-drain-hold.md](core-update-drain-hold.md).

## Plugin?

No: it sits inside core admission paths.
