# Pacing-aware admission, routing and priority edits

Mirror entries: 4459, 4461, 4488, 4503, 4514, 4524, 4549, 4552.

## Behavior

- **4459** — Dispatcher maintenance refreshes configured pacing pools before admission, including idle and capped ticks.
- **4461** — Automatic failure recovery does not promote triaged work with a pending spend decision.
- **4488** — A sufficiently distant or unknown pacing hold can move eligible routes to an unheld, unwalled same-class peer.
- **4503** — The `xai-oauth` provider joins default pacing through an OAuth weekly subscription-usage reader.
- **4514** — Core-update hold notices apply only to cards this host actually leases; remote and pool work retain their actual queue/pacing explanation.
- **4524** — Valid observed land-turn owners rank ahead of nonowners even when age would otherwise dominate capped priorities.
- **4549** — The guarded CLI edit path requires a reason to raise a ready card whose candidate pools are all paced or held.
- **4552** — The capability-drop ready-episode regression pins its clock so same-second claim boundaries are deterministic.

## How it works

Maintenance calls `refresh_pools` through the same shared/in-memory usage-read budget as claim admission. Disabled and dry-run behavior remains unchanged; failed observations become UNKNOWN and stale stored reads remain rejected.

The reroute reader uses fresh stored HELD pools only, not new usage calls. With a reset further away than `kanban.llm_pacing.reroute_after_s` (default 3600 seconds), or no known reset, default, unpinned-review and nonmandatory concrete-pin routes may select an eligible same-class peer. The in-memory decision records a `+paced:<provider>` tier reason without changing the persisted pin. Queue notes explain the move.

The xAI reader calls the Grok CLI billing endpoint with OAuth credentials, maps only a valid weekly percentage to an account-wide `Current week` window, and refuses to follow redirects. Read-only mode without an explicit token does not resolve or rotate credentials.

Turn-owner rank is a separate leading sort key in local and cross-board admission. Owners retain score order among themselves. Missing, expired or invalid observations identify no owners. Spend-decision holds remain decision-bound, rather than being erased by old failure recovery.

## Code

- `agent/account_usage.py`
- `agent/account_usage_xai.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_admission_score.py`
- `hermes_cli/kanban_board_share.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_edit.py`
- `hermes_cli/kanban_dispatch_maintenance.py`
- `hermes_cli/kanban_llm_pacing.py`
- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_paced_reroute.py`
- `hermes_cli/kanban_pacing_defaults.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/kanban_pin_reroute.py`
- `hermes_cli/kanban_queue_position.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Source suites cover zero-admission freshness, remote-only notices and failure budgets; pending spend stops after older failures; default/review/pinned reroutes and exclusions; weekly usage parsing, token-free read-only behavior and redirect refusal; local versus pool hold notices; cross-board and worker pickup ordering; guarded CLI priority refusal, reason persistence and atomic nonapplication of other fields. The clock-only entry retains old-episode and same-second claim controls.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/gateway/test_kanban_cross_board_slots.py`
- `tests/hermes_cli/test_kanban_drain_wait_notice.py`
- `tests/hermes_cli/test_kanban_edit_priority_paced.py`
- `tests/hermes_cli/test_kanban_label_backlog_wait.py`
- `tests/hermes_cli/test_kanban_llm_pacing.py`
- `tests/hermes_cli/test_kanban_llm_pacing_xai.py`
- `tests/hermes_cli/test_kanban_model_tier_paced_reroute.py`
- `tests/hermes_cli/test_kanban_notice_pacing_admission.py`
- `tests/hermes_cli/test_kanban_pacing_idle_tick.py`
- `tests/hermes_cli/test_kanban_pacing_recovery.py`
- `tests/hermes_cli/test_kanban_spend_budget_hold.py`
- `tests/hermes_cli/test_kanban_turn_owner_pickup.py`
- `tests/hermes_cli/test_kanban_turn_score.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

PACED pools with nonzero admission are not rerouted merely for pacing. Mandatory pins, class selectors and `require_model` retain their route; no eligible peer means no move. Unknown usage remains fail-open, not proof of headroom. Priority does not lift a pacing hold: the edit guard is advisory/fail-open on read failure and is not enabled by default for dashboard/REST library callers. A reason permits the edit, not a bypass of admission.
