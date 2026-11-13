# TUI gateway Kanban notice custody and sweep logging

Patches: **3379, 3398**.

## Behavior
- The legacy owner probe and the gateway's claim now read a chat's work ledger by one lineage rule (`lineage_ledger_status`): order-independent, any non-terminal row outside the forwardable states answers with its status (the legacy leg keeps the chat), else the first forwardable status in sorted id order. This fixes a split where the legacy leg handed a chat back while the gateway never claimed it.
- The restore gap is documented on `gateway_owner`: an unrestored chat waits for its ledger work to end, bounded by one owner TTL, and the notice is claimed exactly once.
- The remote-notice sweep logs why it skips an undelivered notice (claim in flight under a backend pid, chat open in this backend, left to the gateway's FIFO) once per chat/ref/state/reason, and logs which backend's live poller claimed a notice once per reason.
- A busy non-owner backend hands a remote claim back on its next poll once the gateway owns the chat (pinned by test).

## How it works
- 3379 adds `lineage_ledger_status` to `tui_gateway/kanban_leg_custody.py`, called by both `gateway/kanban_tui_owner.py` and the gateway claim path.
- 3398 adds once-per-reason logging to `_deliver_link` and `collect` in `tui_gateway/kanban_remote_notices.py`.

## Code
- `tui_gateway/kanban_leg_custody.py`
- `gateway/kanban_tui_owner.py`
- `tui_gateway/kanban_remote_notices.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_tui_sibling_custody.py`: `test_nit1_tip_queued_and_root_unknown_answer_one_rule_on_both_legs`, `test_restore_gap_unrestored_gateway_chat_is_back_within_one_ttl_once_its_work_ends`, `test_gateway_launch_profile_reads_a_real_gateway_state_json`, `test_busy_non_owner_backend_hands_a_remote_notice_back_when_the_gateway_owns_the_chat`.
- `tests/tui_gateway/test_kanban_remote_notices.py`: `test_every_silent_sweep_skip_names_its_reason_once` (per the source message; some names above may live in either file).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- An unrestored chat can wait up to one owner TTL for its ledger work to end.
