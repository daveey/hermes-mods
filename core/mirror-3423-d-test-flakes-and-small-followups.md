# Test deflakes and small review follow-ups

Patches: **3116, 3119, 3120, 3133, 3163, 3171**.

## Behavior
- The managed-worker group-close reap check counts a dead (`X`) process as gone, removing a CI flake.
- The peer member gateway fixture in the groups test boots on an API-server port it owns, removing a port-race flake.
- The `--hub-reason` CLI help is pinned to the `hub_reason` tool schema description.
- `descends_from_live_pid` accepts a pid or its decimal string, refuses init and non-numbers without raising, and refuses non-integral floats instead of truncating them; integral floats are accepted.
- A plain, non-review card's proven worker shell completes its own live-claimed run without force.
- Completion admission recovery tests start the second reconcile fresh and the one-admission-per-identity assumption is documented.

## How it works
- 3116 treats `STATUS_DEAD` as gone and asserts via `wait_until`. 3171 binds the member's API-server port without a free-then-close window.
- 3119 adds a parity test. 3120 documents the `int()` coercion and adds tests; 3133 refuses non-integral floats and skips a pid test under an init parent.
- 3163 clears in-memory offer state before the second reconcile and adds docstrings in `requeue_admitted_completion` and the recovery module.

## Code
- `hermes_cli/top_level_env.py`
- `gateway/completion_admission_recovery.py`, `tools/async_delegation.py`
- `tests/gateway/test_managed_worker_group_close.py`, `tests/gateway/test_session_group_peer_daemons.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/test_kanban_hub_admission_entry.py`: `test_cli_hub_reason_help_matches_tool_schema`.
- `tests/hermes_cli/test_leaked_worker_env.py`: `test_descends_from_live_pid_accepts_a_pid_or_its_decimal_string`, `test_descends_from_live_pid_refuses_init_and_non_numbers_without_raising`, `test_descends_from_live_pid_refuses_non_integral_floats_rather_than_truncating`.
- `tests/tools/test_kanban_review_contract.py`: `test_cli_worker_shell_completes_a_plain_card_without_force`.
- `tests/gateway/test_managed_worker_group_close.py`, `tests/gateway/test_session_group_peer_daemons.py`, `tests/gateway/test_completion_admission_desktop_delivery.py`: changed; no new test names read from the patches.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
