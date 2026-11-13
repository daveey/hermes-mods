# Kanban review independence and worker-shell completion

Patches: **3086, 3094, 3104, 3106**.

## Behavior
- A review verdict is refused from an outside caller when the card body names a core pull or when the completion's own `reviewed_pr` names one; the card's own dispatched worker, landing handoffs, verdictless handoffs and non-core values are unaffected.
- A card's own worker shell can complete its run through the CLI without `--force`: the shell is proven by descent from the run's live worker interpreter or fingerprinted spawn pid. A fingerprint mismatch or pid 1 is refused.

## How it works
- 3086 adds `kanban_worker_shell.run_for` and relaxes the independence refusal for a proven worker shell.
- 3094 shares `top_level_env.descends_from_live_pid` and tests the spawn-pid fallback.
- 3104 adds the `reviewed_pr` fallback to `independence_refusal`; 3106 documents it.

## Code
- `hermes_cli/kanban_review_contract.py`
- `hermes_cli/kanban_worker_shell.py`
- `hermes_cli/top_level_env.py`
- `hermes_cli/kanban.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_kanban_review_contract.py`: `test_cli_from_the_cards_worker_shell_completes_its_own_run`, `test_cli_worker_env_alone_is_not_trusted`, `test_cli_worker_env_on_an_unclaimed_card_is_refused`, `test_cli_worker_env_for_another_card_keeps_the_outside_advice`, `test_cli_worker_shell_proven_by_the_fingerprinted_spawn_pid`, `test_cli_worker_shell_spawn_pid_fallback_refuses`, `test_independence_refuses_outside_verdict_named_only_by_reviewed_pr`, `test_reviewed_core_pull`, `test_tool_refuses_outside_approval_named_only_by_reviewed_pr`, `test_cli_refuses_outside_approval_named_only_by_reviewed_pr`, `test_tool_dispatched_worker_completes_prose_review_card`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
