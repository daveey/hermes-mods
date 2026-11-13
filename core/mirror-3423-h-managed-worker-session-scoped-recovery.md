# Managed worker adopts only its own session's orphaned jobs

Patches: **3384, 3402**.

## Behavior
- A Kanban managed worker's startup recovery adopts only orphaned background jobs belonging to its own route and session; entries of a Desktop chat or other routes are left for their owners' recovery (a gateway or Desktop backend keeper, or a later run of the same route).
- This prevents a worker from stealing a chat's long-running job, which previously fenced the successor backend's keeper and sent the completion to a queue nobody read, so the chat never got its notice.
- Gateway and Desktop recovery are unchanged. Keyless orphans are not adopted by a managed worker.

## How it works
- 3384 gives `recover_from_checkpoint` a `session_keys=` argument; the managed worker passes its route and session id.
- 3402 documents the narrowed scope at the call site and lets the `_execute` test helper take an optional `recover` stub (replacing a monkeypatch swap).

## Code
- `agent/managed_worker.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_managed_worker_checkpoint_scope.py`: `test_managed_worker_leaves_a_desktop_chats_orphan_to_the_desktop_backend`, `test_session_scoped_recovery_leaves_other_entries_untouched`.
- 3384 also touches `tests/agent/test_managed_worker_observation_bind.py`, `tests/agent/test_managed_worker_observer_load.py`, `tests/gateway/test_bot_chat_follow_entry.py` and `tests/gateway/test_managed_turn_contract.py`; 3402 touches `tests/gateway/test_managed_worker_route_key.py`. No new test names were read from those.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Orphans with no session key, or belonging to other worker routes, wait for their own owner or a later run of the same route.
