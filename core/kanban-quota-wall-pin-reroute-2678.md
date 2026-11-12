# Fresh pinned card on a live quota wall reroutes in class; storm alert fixes

Patches: **2627, 2663**.

## Behavior

- A card with an explicit model pin and no earlier run, whose pin's provider pool is quota-walled right now in the live pool, now counts that wall as evidence for the in-class pin reroute. If the wall resets `kanban.pin_quota_reroute_min_hours` (default 6) or more from now, the pin is not mandatory, the lane is ready and a same-class peer is eligible, its first run goes to the peer instead of waiting the wall out or spawning into it. The card row keeps its pin; after the reset the pin is used again.
- The reroute writes a `model_reroute` event whose `resets_at` is the live reset and whose `episode_id` is null, plus one comment naming the walled model and the reset. A later run of the same card still avoids the wall, because the card's own earlier `model_reroute` events now count as walls it knows about.
- Where the reroute does not apply (mandatory pin, wall shorter than the minimum, no eligible peer), the card is held with one `dispatch_skipped` event, reason `pinned_quota_wall` (see [kanban-dispatch-skip-reasons-2678.md](kanban-dispatch-skip-reasons-2678.md)); board diagnostics show `held_in_ready` (warning) with the hold deadline.
- The "mandatory pin" body marker is now a deliberate line `require_model: true` (case-insensitive, optional spaces, also `yes` or `1`), not any mention of `require_model` in the title or body. `require_model: false` and prose mentions no longer pin the card. `tasks.model_pin_mandatory` is unchanged.
- The rate-limit storm alert counts only `rate_limited` runs claimed by this host (claim lock, or `metadata.claimer` on synced rows, starts with the host's claim prefix); runs with no claimer count for nobody. The alert body lists walls as `YYYY-MM-DD HH:MMZ` (UTC) instead of local `HH:MM`, and no longer contains the marker word, so the alert card is not opted out of the wall reroute it reports.

## How it works

- 2627: `kanban_pin_reroute.fresh_live_wall(conn, task, now, pinned)` returns `{provider: reset}` when the task had no run before its current one and `kanban_provider_walls.live_quota_wall` reports the pin walled. `kanban_model_tier.resolve_dispatch_tier` passes it to `pinned_route` as `current`, so preview and admission reroute the first run; `record()` falls back to it to name the live reset in the comment and event. `own_walls` also reads earlier `model_reroute` events (`walled_provider`, `resets_at`, `episode_id`). `kanban_dispatch_skip.pinned_quota_hold` holds only when `pinned_route` would return nothing, and holds if the policy probe raises a non-SQLite error. `kanban_diagnostics` adds `_pinned_quota_hold_until` and a `pinned_quota_wall` hold reason with its own detail text.
- 2663: `kanban_provider_walls.requires_model(task)` matches body lines with a multiline regex and replaces the substring test in `retier_for_walls`, `kanban_model_tier._class_alternate_off_wall` and `kanban_pin_reroute.is_mandatory_pin`. `rate_limit_storm(conn, now, prefix)` filters runs by claim prefix (`kanban_db._host_prefix()` by default); `_utc` formats walls in `alert_on_storm`. The user-guide configuration text in `website/docs/user-guide/configuration.md` is updated to match.

## Code

`hermes_cli/kanban_pin_reroute.py`, `hermes_cli/kanban_model_tier.py`, `hermes_cli/kanban_provider_walls.py`, `hermes_cli/kanban_dispatch_skip.py`, `hermes_cli/kanban_diagnostics.py`, `website/docs/user-guide/configuration.md`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pin_reroute.py`, `tests/hermes_cli/test_kanban_provider_walls.py`, `tests/hermes_cli/test_kanban_dispatch_skip.py`. Entry path: a dispatcher tick over a temporary board with the live-wall probe stubbed, `compute_task_diagnostics`, the storm-alert functions, and `requires_model` / `is_mandatory_pin` directly. Covered: a fresh far-wall pin rerouting on its first run, staying on the peer for its next run and returning to the pin after the reset; mandatory, near-wall and no-peer fresh pins held with one skip and a `held_in_ready` warning; the `require_model: true` line (and the flag) making a pin mandatory; prose mentions and `false` not doing so; the storm count by host prefix (including synced rows and a prefix-lookalike host); the alert body not opting itself out; wall times in UTC under a non-UTC timezone. Entry paths **untested**: the `hermes kanban set-model` and `--pin-mandatory` CLI flags, and a real provider pool (the live wall is stubbed).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A card whose body only mentions `require_model` (for example in a title or in prose) is no longer treated as mandatory; cards relying on the old substring need a line `require_model: true`.
- The storm count is per host, so a board-wide storm now files one alert per affected host, each counting only its own claimed runs.
- If every peer in the class is walled or disabled, or the policy has none, the fresh pin still waits for the wall.

## Plugin

Core change; no plugin required.
