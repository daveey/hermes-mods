# Test hardening: disposable host policy for owner probes, placeholder chats in links_diff

Patches: **3628, 3629**.

## Behavior
- Tests only, no production code. The real-dispatcher kanban probes now pass on a worker host that has an account-level worker-resources policy, where they previously failed.
- A new test pins that `kanban-placement*` placeholder chats are counted but not compared in the chat-links diff.

## How it works
- 3628 adds `local_recovery_probe.isolate_host_policy(home)`, pointing the policy root at `<home>/host-policy` in the probe process and, through a `host_policy/sitecustomize.py` on child PYTHONPATH, in every Python child. Children drop a marker; tests assert the receipt's policy root is under the temp dir and that children read it.
- 3629 adds a test seeding a placeholder chat beside a human chat, asserting one machine link and that only the human chat differs; it catches removal of the placeholder filter in `_unlinked_chats`.

## Code
- `tests/gateway/fixtures/host_policy/sitecustomize.py`
- `tests/gateway/fixtures/kanban_owner_probe.py`
- `tests/gateway/fixtures/kanban_recovery_probe.py`
- `tests/gateway/fixtures/kanban_restart_continue_probe.py`
- `tests/gateway/fixtures/local_recovery_probe.py`
- `tests/gateway/test_kanban_recovery.py`
- `tests/gateway/test_kanban_restart_continuation.py`
- `tests/gateway/test_session_kanban.py`
- `tests/hermes_cli/test_kanban_chat_links_parity.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_chat_links_parity.py`: `test_links_diff_leaves_out_placement_placeholder_chats`.
- `tests/gateway/test_kanban_recovery.py`, `tests/gateway/test_kanban_restart_continuation.py`, `tests/gateway/test_session_kanban.py`: existing tests (`test_owner_board_recovery`, `test_a_real_owner_restart_mid_turn_continues_the_same_run`, `test_real_dispatcher_lifecycle`) modified to assert the isolated policy root.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Isolation covers only Python children that import the worker capacity module via the sitecustomize hook.
