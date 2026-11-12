# Notifier wakes held during core drain; claim waits split

Patches: 2844, 2845, 2846, 2847, 2848.

- **2844** (`bc3b626bc2`, Defer notifier batches and queued wakes during core drain)
- **2845** (`f7c08f11af`, Update drain claim fixtures without weakening retirement checks)
- **2846** (`ee08173ae6`, Compose held notifier selection with update checkpoints)
- **2847** (`1ddefc0edf`, Split the claim-retry wait states out of _claim_with_retry)
- **2848** (`a5cd70aac2`, A wake carrying its Kanban card stays a kanban-wake so queued wakes still fold)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2844: notifier wakes are tagged `core_update_notifier=True`; while a core-update drain holds, the notifier decides once before any delivery and rewinds its cursor (no mid-batch replay), a human wake bypasses the hold, and `session_notifier_drain` (`held`, `eligible`, `queued_head`) filters notifier rows from queued selection; non-notifier inputs are never filtered. `LiveSession.claim_changed` wakes waiters.
- 2845: drain claim test fixtures forward the attempted identity and model `claim_changed` without weakening retirement assertions (test-only).
- 2846: a started update checkpoint keeps precedence; only queued selection skips held notifier wakes; attempted identity and validated queued selection stay distinct. If a conflicted head is cancelled mid-check the drain re-reads and runs the follower instead of pausing with it stranded.
- 2847: the update_paused / notifier_held / classic_busy waits move into `_wait_claim_state` from `_claim_with_retry` (complexity cap), unchanged.
- 2848: a wake with a Kanban card but no explicit flag is producer `kanban-wake`, so queued wakes still fold.

## How it works

- `gateway/kanban_watchers_notifier.py`, `gateway/session_authority.py`, `gateway/session_automation.py`, `gateway/session_notifier_drain.py` (new), `gateway/wake.py`, `hermes_state_runtime.py`.

## Code

- `gateway/kanban_watchers_notifier.py`
- `gateway/session_authority.py`
- `gateway/session_automation.py`
- `gateway/session_notifier_drain.py`
- `gateway/wake.py`
- `hermes_state_runtime.py`

## Tests

- `tests/gateway/test_notifier_core_drain.py`, `test_notifier_fallback_drain.py`, `tests/hermes_state/test_notifier_claim_eligibility.py` (hold/release, human bypass, exact-head flip, window fence), `test_notifier_checkpoint_precedence.py`, `test_conflict_cancel_successor.py`, `test_update_pause_refusal.py`, `test_hosted_room_retired_admission.py`, `test_local_closed_retirement.py`, `test_runtime_worker_shutdown.py`, `test_kanban_wake_fold.py`.

Test files touched by these patches:

- `tests/gateway/test_notifier_core_drain.py`
- `tests/gateway/test_notifier_fallback_drain.py`
- `tests/hermes_state/test_notifier_claim_eligibility.py`
- `tests/gateway/test_hosted_room_retired_admission.py`
- `tests/gateway/test_local_closed_retirement.py`
- `tests/gateway/test_runtime_worker_shutdown.py`
- `tests/gateway/test_conflict_cancel_successor.py`
- `tests/gateway/test_notifier_checkpoint_precedence.py`
- `tests/gateway/test_update_pause_refusal.py`
- `tests/gateway/test_kanban_wake_fold.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Held notifier wakes stay queued with their cursor retained, so a long drain delays but does not drop notices. 2847 is a pure move.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
