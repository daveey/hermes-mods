# Kanban pacing capacity model v2 (replaces the per-host start limit)

Mirror entries 4953–4957.

## Behavior

- Each pool refresh plans the token rate every governed pool can spend so each account reaches its reserve
  (`margin_seconds`, default 10800 s) before its weekly reset. The plan is stored in `llm_pacing.json` as `"version": 2` /
  `"v2"` beside the unchanged legacy provider keys (which runway still reads).
- The per-host **start limit** is removed (`pace_starts`, `deferrable_share`, `admit_limit`, running samples,
  `is_deferrable`, picked/admitted counters). Holds now come only from `v2.pools[p].at_reserve`, and only when fresh; no v2 means no hold.
- `budget_publish` defaults to false and the tokens-per-percent knobs are config; core publishes nothing to the throttle.
- Hardening: reset times are clamped to `[now, now + window + 1 day]` and the curve is capped at 64 points, so a far-future
  reset cannot make planning expensive. A non-finite `used` reads as 100% (exhausted) rather than "no reading", which preserves the legacy held verdict.
  A malformed stored v2 is treated as absent and the update fails open in layers (history -> last rates; plan -> legacy reserve rule).
- 4956 refactors a mode if/elif ladder in a dispatch-level test into a table of setups; 4957 marks the history store as not a board and fixes a turn-owner test.

## How it works

- `kanban_llm_pacing` is a pure model: tokens-per-percent estimate with EWMA, events, per-account and pool rates with the
  five-hour cap, the curve, the ratio and `route_ratios`. One exchange rate per pool (`plan_size_assumed_equal`); model-scoped Anthropic windows never change capacity.
- `kanban_llm_pacing_history` keeps `llm_pacing_history.sqlite`: 8 days of hashed samples, fleet token totals from per-machine
  increases, events; no labels stored. One connection per refresh, `user_version` set only on creation, one write transaction with a 0.5 s lock timeout; a busy store skips history for that refresh.
- `machine_throughput_metrics.provider_counters` supplies tokens per billing provider (model-prefix fallback) off the 10 s metrics path, never sent to the Board.
- `valid_readings` stores a non-finite `used` as `100.0` so no NaN/inf reaches JSON.
- The history connect carries the documented `noqa: kanban-lockguard` marker because it opens a pacing file, not a board.

## Code

`hermes_cli/kanban_llm_pacing.py`, `kanban_llm_pacing_dispatch.py`, `kanban_llm_pacing_history.py`, `kanban_pacing_defaults.py`,
`kanban_dispatch_expected_holds.py`, `kanban_start_gate.py`, `kanban_worker_node.py`, `machine_throughput_metrics.py`,
`config_defaults.py`, plus `website/docs/user-guide/features/kanban.md`.

## Tests

Source tests (not executed here): `test_kanban_llm_pacing_v2`, `_capacity`, `_persistence`, `_robustness`, `_replay` (with a synthetic replay fixture),
`_empty_windows`, `_followups`, `_missing_reset`; `test_dispatch_expected_holds`, `test_kanban_pickup_paced_window`,
`test_kanban_pickup_pacing_headroom`, `test_kanban_model_tier_paced_reroute`, `test_kanban_pacing_recovery`, `test_kanban_pacing_idle_tick`,
`test_kanban_notice_pacing_admission`, `test_kanban_edit_priority_paced`, `test_kanban_turn_owner_pickup`.

## Applying

Do not apply individual patches. Follow the full segmented recipe in the core mods [README](README.md) ("Applying").
These five entries touch the same files and must go in order.

## Gotchas

- Removing the start limit changes dispatch semantics: tests that waited on a paced start limit must wait on a pool at reserve instead.
- Anything that read `llm_pacing.json` without a v2 section must tolerate absence.
- Throttle publication is off by default; enabling it is an operator decision.
