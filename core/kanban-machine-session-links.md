# Human chat ownership excludes machine sessions

Patch **1659** (`03e986dad31411c905a0a7c577b326bcac307362`).

## Behavior

Creator-chat resolution skips worker, cron, one-shot and subagent transcripts and continues walking to a human chat. An all-machine ancestry stays unresolved rather than assigning ownership to an unattended transcript. Notification subscription inheritance uses the same exclusion. Reconciliation releases obsolete machine links through captured writes, including owner-written links on another gateway's open cards.

`hermes kanban sync machine-links` previews the repair; `--release` reconciles it. A release pass records an audit receipt under `kanban/repairs/machine-links-<board>-<timestamp>-<count>.json` in the Kanban home.

## How it works

`machine_sessions()` batches local `state.db` source lookups; `is_machine_session()` is its single-session wrapper. Headless receipt IDs and `cron_` IDs are excluded by shape even without a local state row. Other IDs are excluded only when their gateway is local and `sessions.source` is `kanban`, `cron`, `oneshot` or `subagent`. Found sources are cached; missing rows in readable stores expire after 300 seconds. Unknown or foreign sources are not guessed.

`resolve_chats()` applies the predicate to ordinary links and the hub-subscription branch. `_inherit_notify_subs()` excludes machine subscriptions. The reconciliation plan omits machine ownership, releases existing rows and reports machine releases separately from older headless-link retirement.

## Code

- `hermes_cli/kanban_db_notify.py`: classification and subscription inheritance.
- `hermes_cli/kanban_creator_chats.py`: ancestry traversal.
- `hermes_cli/kanban_chat_links.py`: planning, reconciliation and receipts.
- `hermes_cli/kanban_parser.py`, `hermes_cli/kanban_sync_cli.py`: repair command.

## Tests

Source tests in `tests/hermes_cli/test_kanban_machine_session_links.py`:

- `test_a_nearest_local_machine_link_resolves_to_the_human_chat_above`
- `test_a_hub_sub_branch_skips_a_local_machine_origin`
- `test_a_child_does_not_inherit_a_machine_sessions_owning_sub`
- `test_b_a_chain_of_only_machine_links_is_unresolved`
- `test_c_a_cron_id_is_machine_without_a_state_row`
- `test_d_replica_link_released_by_the_chats_gateway_is_skipped`
- `test_e_repair_dry_run_then_release_then_nothing`

These are patch-contained tests, not rerun in this documentation mirror. They cover local DB/replica fixtures and the repair CLI, not a live cross-gateway rollout.

## Applying

Apply 1659 in the ordered mirror series using the [README replay instructions](README.md). It depends on the existing creator-chat traversal, synced chat links and notification schema; it is not a standalone plugin.

## Gotchas

A foreign timestamp-style ID cannot be classified from this gateway's `state.db`; it remains eligible until its owning gateway publishes a release. A receipt write failure logs a warning but does not undo the committed release. Receipts contain operational identifiers and should not be copied into public documentation.
