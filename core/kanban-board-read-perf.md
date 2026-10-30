# Kanban board prewarm and per-scope attention caches

Patches:

- `patches/1086-perf-kanban-prewarm-the-board-at-boot-and-drop-diagn.patch`
- `patches/1109-perf-kanban-cache-attention-counts-per-origin-scope-.patch`

Read-path performance for the Kanban dashboard and the profile project tree.

## Behavior

- 1086: the board is prewarmed at boot and diagnostics are dropped for trimmed done cards (default-board cold read 5.1-5.7 s to 0.25-0.37 s, per the commit).
- 1109: `attention_task_counts` caches per origin scope (bounded at 64 scopes) and done-check verdicts are shared by (visible boards, board, card id, completed_at, title, summary), so a tree poll over many profiles no longer re-runs every check (tree 1.1 s to 0.1 s on the hub's boards, per the commit).

## How it works

Dashboard code warms the snapshot at startup; the attention module keeps a scope-keyed TTL cache.

## Code

`plugins/kanban/dashboard/board_diagnostics.py`, `board_prewarm.py`, `board_snapshot.py`, `plugin_api.py`, `hermes_cli/kanban_session_activity.py`, `hermes_cli/kanban_session_activity_attention.py`.

## Tests

`tests/plugins/test_kanban_board_read_cache.py` (1086) and `tests/hermes_cli/test_kanban_attention_scope_cache.py` (1109). The timings quoted are the author's measurements and were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.
