# Kanban: a chat's verified relay of the user's reply counts as human progress

Patches: **2997–3001**.

## Behavior
- A comment with `relays_user_message=true` from an interactive chat (not a worker or child) is verified and recorded as `relayed_human` on the commented event, resetting the no-progress budget; the writer stays an agent.
- Verification requires a message a person typed (synthetic rows such as notices, compaction summaries, task-list snapshots, wakeup frames and cross-session deliveries are skipped), newer than the card's latest ask, one relay per message per card.
- The dashboard comment endpoint accepts `relayed_human` from a courier and refuses replayed or pre-ask relays (HTTP 400, with a 120 s remote clock skew allowance).
- Unmarked relays still count, and an escalation right after a chat unblocked a needs_input block logs `no_progress_relay_suspect`.
- A reply after an interrupted /loop tick still counts; the /heartbeat frame is synthetic.

## How it works
- 2997 adds `hermes_cli/kanban_relayed_human.py`, `progress_marker` support in `kanban_no_progress.py`, and the dashboard comment actor path.
- 2998 selects the newest typed user row and adds `admit()`, called atomically inside `add_comment` and first by the dashboard endpoint.
- 2999 sources excluded openings from the producers' registries and skips a running slash-loop tick's row; the storeless kanban notice turn is typed `kanban_notice`.
- 3000 limits the slash-loop skip to the first untyped row at or after the fire and adds the heartbeat frame.
- 3001 reads state.db past the board lock guard (noqa), records the plugin import in the boundary baseline, and finds the tick row with the same filters as the reply scan.

## Code
- `hermes_cli/kanban_relayed_human.py`
- `hermes_cli/kanban_no_progress.py`
- `hermes_cli/kanban_db_comment_ids.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `plugins/kanban/dashboard/plugin_api_comment_actor.py`
- `tui_gateway/session_notifications.py`
- `scripts/boundary_lint_baseline.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_no_progress.py`: `test_relayed_answers_reset_the_budget`, `test_unmarked_relays_still_count_and_flag_a_suspect`.
- `tests/hermes_cli/test_kanban_relayed_human.py`: `test_verify_accepts_one_newer_user_message_per_card`, `test_verify_refuses_a_reply_older_than_the_ask`, `test_verify_refuses_workers_and_sessionless_callers`, `test_relay_requires_agent_writer`, `test_kanban_comment_tool_records_a_verified_relay`, `test_kanban_comment_tool_refuses_a_worker_relay`, `test_remote_relay_is_stored_as_sent`, `test_verify_skips_synthetic_user_rows`, `test_a_notice_after_the_ask_with_no_reply_is_refused`, `test_a_steer_is_a_typed_reply`, `test_card_holder_refuses_remote_replay_pre_ask_and_unasked`, `test_a_running_slash_loop_ticks_prompt_is_not_a_reply`, `test_every_registered_synthetic_opening_is_excluded`, `test_a_reply_after_an_interrupted_loop_tick_is_the_reply`, `test_an_untyped_row_of_a_wakeup_framed_loop_is_never_skipped_as_the_tick`, `test_a_synthetic_row_before_the_tick_row_is_not_taken_as_the_tick`.
- `tests/tools/test_kanban_dashboard_comment_actor.py`: `test_courier_relay_of_the_user_reply_is_stored_as_sent`, `test_relay_without_agent_kind_or_malformed_is_refused`, `test_receiver_refuses_a_replayed_or_pre_ask_relay`.
- `tests/tui_gateway/test_kanban_notice_at_least_once.py`: `test_storeless_notice_turn_is_still_typed_kanban_notice`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Later patch 3032 types slash-tick rows and narrows the legacy first-untyped-row skip introduced here.
