# Secret-safe wake checks and live-claim park protection

Patches: **2732, 2778, 2782**.

Related context: [kanban-stale-block-ask-refresh-2678.md](kanban-stale-block-ask-refresh-2678.md).

## Behavior

CLI `hermes kanban block/schedule --wake-check` and tool park paths refuse a probe that forced secret redaction would change, before executing its dry run or storing it. Refusal is `wake_check_secret` (CLI exit 2), not a silently masked executable command. Tool `kanban_schedule`/`kanban_block` also refuse an outside park of a card owned by a live worker, preserving its open run.

## How it works

2732 centralizes `secret_error` in `kanban_wake_check_secret.py` for CLI and tool use. 2778 tests `force=True` even when environment/config redaction is disabled, omits the assignment's `=` from error wording so the redactor does not consume following prose, and adds `wake_check_secret` to identifier-key names. Nonsecret idempotency-key probes stay byte-identical. 2782 adds `kanban_tools_live_park.refusal`, called before run-end writes. An outside caller lacking matching task/run ownership gets `live_claim_park` with the claimer when a running card has a live local worker, an unexpired foreign lease, or an unexpired Board claimed event for the current open run—even before its lease mirror arrives. Own-worker parks, unclaimed ready cards, dead workers and expired Board claims remain permitted.

## Code

- `agent/redact_identifier_keys.py`
- `hermes_cli/kanban_cli_park.py`
- `hermes_cli/kanban_wake_check_secret.py`
- `tools/kanban_tools_live_park.py`
- `tools/kanban_tools_park_args.py`
- `tools/kanban_tools_run_end.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_cli_probe_secret.py`: `test_cli_park_with_inline_secret_is_refused_and_stores_nothing`, `test_cli_secret_refusal_runs_before_the_dry_run`, `test_cli_park_stores_idempotency_key_probe_byte_identical`.
- `tests/hermes_cli/test_kanban_outside_park_board_claim.py`: `test_creator_park_before_the_lease_mirror_arrives_is_refused_naming_the_claimer`, `test_an_expired_board_claim_does_not_hold_off_an_outside_park`.
- `tests/hermes_cli/test_kanban_wake_check_secret_forced.py`: `test_secret_error_refuses_with_redaction_off`, `test_cli_schedule_refuses_with_redaction_off`, `test_tool_schedule_refuses_with_redaction_off`, `test_refusal_text_is_stable_under_the_output_redactor`, `test_refusal_names_the_assignment_without_its_value`.
- `tests/tools/test_kanban_outside_park_live_claim.py`: `test_outside_schedule_of_a_live_claimed_card_is_refused_and_the_run_stays_open`, `test_outside_block_of_a_live_claimed_card_is_refused`, `test_the_worker_still_parks_its_own_live_card`, `test_an_unclaimed_ready_card_is_still_parked_from_outside`, `test_a_claim_whose_worker_is_gone_can_still_be_parked_from_outside`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

Never inline credential values in a wake check; reference the probe host's environment or a file-backed helper. Turning display redaction off is not an escape from probe safety. The live-claim protection here is the tool path; CLI outside-park parity is not introduced or proven by 2782. Steer a running owner through a comment instead of closing its run from another chat. Real Board timing/network behavior was not tested by this mirror pass.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
