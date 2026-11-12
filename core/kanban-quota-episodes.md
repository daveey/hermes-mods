# Kanban quota episodes and clock-held dispatch warnings

Patches: 1592 1594 1595 1606 2716 2717

- `patches/1592-fix-kanban-stuck-warning-names-clock-held-deadlines-.patch`
- `patches/1594-daveey-kanban-honour-quota-wall-reset-deadlines-per-.patch`
- `patches/1595-daveey-kanban-quota-episode-exit-trailer-test-reads-.patch`
- `patches/1606-fix-kanban-quota-episode-routing-checks-walls-it-did.patch`

Related: [kanban-provider-quota-walls.md](kanban-provider-quota-walls.md) covers provider-wide wall routing; [subscription-quota-deadlines.md](subscription-quota-deadlines.md) covers typed reset propagation; [kanban-quota-recovery.md](kanban-quota-recovery.md) covers quota-neutral recovery.

## Behavior

- **1592 — clock-held warnings:** the CLI daemon and embedded gateway dispatcher do not count a zero-spawn tick as stuck when every enumerated lane candidate waits on a known deadline. Mixed clock/non-clock holds still warn. Suppression details show `reason=count (until <UTC timestamp>)`, or an earliest-to-latest range across cards.
- **1594 — durable episodes:** consecutive ended `rate_limited` runs form a per-card episode. Exact-attempt reset hints survive reaping and hold admission beyond the ordinary cooldown. Without a usable hint, backoff doubles per run, capped at two hours; short hints cannot undercut that backoff. A completed or crashed non-wall run ends the episode. Quota requeues do not consume the failure budget.
- **1594 — shared routing decision:** the respawn guard, read-only route preview and dispatch agree on an eligible class alternate for unpinned default/profile-default cards. Dispatch records `+quota_reroute` without persisting a new task pin. Explicit pins and strong/review routes are not automatically rewritten; a known free provider selected by a changed pin can be admitted. Unknown provider custody holds rather than guesses.
- **1594 — notices:** the third consecutive quota requeue records one notice and comment per episode, naming a reroute or wait. Gateway notifier delivery and the Desktop/TUI backend notification collector deduplicate it. Long episode cooldowns do not create generic guard-hold fix cards. Actual Desktop rendering and live transport delivery are untested here.
- **1595 — test follow-up:** exit-trailer accounting checks the booking event separately from the subsequent `quota_episode` event. A new unpinned default card avoids a provider wall persisted by an earlier reaped receipt even when the live-pool probe returns no evidence.
- **1606 — external walls and fallback:** during an active episode, a different concrete target is checked against provider-file or live-pool walls before admission, including changed pins and review routes. After an episode expires, ordinary default routing again applies provider-wide wall fallback. If no class alternate exists, an unpinned `default`-tier card can use the configured strong-tier fallback, checked against episode walls too. Tests cover routing disabled, a default absent from classes, and all class routes walled; ambiguous multi-class membership is untested.
- **1606 — episode-aware warning deadline:** quota-reset and rate-limit-cooldown warning deadlines use the latest outstanding deadline within the episode, including an earlier provider's longer wall and doubled backoff, rather than only the latest receipt or one cooldown interval.

## How it works

`DispatchResult.clock_held` maps a guarded task to its reason and deadline. `lane_candidates` counts ready rows, plus review rows when review dispatch is enabled, before eligibility filtering. `all_clock_held()` ignores boards skipped for locks/ownership, requires at least one clock-held card, and requires no spawns and a complete clock-held census on every remaining board. Empty results alone cannot silence a stuck warning.

The reaper records `quota_episode` in the run event stream. Episode identity is the oldest run in the uninterrupted sequence of ended quota runs; polling does not advance the count. Stored state carries the latest run, count, deadline, hint presence and recorded providers. Admission considers still-outstanding earlier episode events as well as the latest state.

`receipt_deadline()` accepts only the exact task/run's quota-exit receipt and checks its claim when one exists. It prefers typed `failure_resets_at`, then relative reset text, timezone-bearing ISO reset text, epoch reset text, and retry-after text. Relative hints are anchored to receipt creation, not polling or reaping time. Compound relative durations are summed; multiple relative windows use the longest. Nonfinite or unrepresentable deadlines are rejected. The final episode deadline is the later of the usable hint and capped exponential backoff.

Worker receipts add concrete `route_provider`/`route_model` from a typed subscription refusal or the live agent. Both admitted and actual recorded providers can belong to a wall; missing custody is not inferred from a class selector. The class alternate probe is non-allocating and shares worker tools/streaming/context requirements, billing eligibility and provider enablement checks. A concrete default must belong to exactly one class to use that probe.

`resolve_dispatch_tier()` shares the episode decision with admission and route preview. Patch 1606 checks external walls for a concrete target not yet encountered by the episode, then tries a class alternate followed by the existing provider-wall strong fallback. That fallback accepts `extra_walls` and is restricted to unpinned ordinary default routing. `hold_until()` supplies the maximum outstanding episode deadline to warning bookkeeping; it does not persist external-wall probe results into the episode.

Episode notices use existing notifier cursors and diagnostic formatting, not an origin wake. Wait text uses Pacific time; stuck-warning timestamps use UTC. Non-database routing/notice errors fail closed or fall back to wait wording so reaping can finish; database errors propagate to the owning transaction.

## Code

- **1592:** `gateway/kanban_watchers.py`, `hermes_cli/kanban_ops.py`, `hermes_cli/kanban_db_dispatch.py`, new `hermes_cli/kanban_db_dispatch_clock.py`, new `hermes_cli/kanban_db_dispatch_stuck.py`, and `tests/hermes_cli/test_kanban_stuck_clock_holds.py`.
- **1594:** `agent/model_routing_agent.py`, `gateway/kanban_watchers_notifier.py`, `gateway/session_kanban.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_guard_notice.py`, `hermes_cli/kanban_model_tier.py`, `hermes_cli/kanban_provider_walls.py`, new `hermes_cli/kanban_quota_episode.py`, `hermes_cli/kanban_quota_receipt.py`, `hermes_cli/kanban_shared_blockers.py`, `tui_gateway/session_notifications.py`, `tests/hermes_cli/test_kanban_provider_walls.py`, and new `tests/hermes_cli/test_kanban_quota_episode.py`.
- **1595:** `tests/hermes_cli/test_kanban_quota_episode.py` and `tests/hermes_cli/test_kanban_worker_exit_trailer.py` only.
- **1606:** `hermes_cli/kanban_db_dispatch_clock.py`, `hermes_cli/kanban_model_tier.py`, `hermes_cli/kanban_provider_walls.py`, `hermes_cli/kanban_quota_episode.py`, `tests/hermes_cli/test_kanban_quota_episode.py`, and new `tests/hermes_cli/test_kanban_quota_episode_walls.py`.

## Tests

The following are test definitions read from the patches, not execution results from this documentation pass.

**1592 — `tests/hermes_cli/test_kanban_stuck_clock_holds.py`:**

- `test_breakdown_names_the_quota_reset_deadline`
- `test_single_deadline_and_cooldown_are_named`
- `test_non_clock_guard_has_no_deadline`
- `test_all_clock_held_only_when_every_ready_card_waits_on_a_clock`
- `test_all_clock_held_edge_cases`
- `test_cli_daemon_silent_when_every_ready_card_waits_on_quota_reset`
- `test_cli_daemon_warns_with_deadline_when_other_cards_are_held`
- `test_gateway_silent_when_every_ready_card_waits_on_quota_reset`
- `test_gateway_warns_with_deadline_when_other_cards_are_held`

**1594 — `tests/hermes_cli/test_kanban_quota_episode.py`:**

- Deadlines and custody: `test_text_reset_hint_survives_reaper_and_blocks_after_cooldown`, `test_deadline_fixed_to_receipt_and_typed_precedence` (ready/review; relative, ISO, epoch, typed and retry-after), `test_unowned_receipt_cannot_supply_reset`, `test_legacy_relative_text_uses_full_window`.
- Backoff and boundaries: `test_backoff_cap_notice_once_and_nonwall_resets`, `test_short_hints_never_undercut_doubling_backoff`, `test_long_cooldown_hold_files_no_generic_fix_card`.
- Routing: `test_shared_admission_preview_and_owner_route`, `test_walled_concrete_default_model_reroutes_to_class_peer`, `test_real_dispatch_waits_or_uses_preview_route`, `test_reroute_notice_once_and_no_return_to_previous_walled_provider`, `test_class_admission_reroutes_only_on_recorded_runtime_provider`, `test_route_fields_prefer_typed_refusal_and_never_record_auto`, `test_reroute_applies_worker_capability_requirements`, `test_changed_explicit_pin_is_judged_by_its_own_provider`.
- Fault handling: `test_unrepresentable_deadline_is_rejected_and_reaper_finishes`, `test_overflowing_retry_after_never_strands_the_sweep`, `test_notice_routing_failure_falls_back_and_reaper_finishes`, `test_database_integrity_error_in_episode_record_propagates`.
- Notification entry paths: `test_quota_notice_delivered_once_by_gateway_notifier` uses a fake gateway adapter; `test_quota_notice_delivered_once_to_desktop_chat` exercises the backend collector and diagnostic type, not a rendered Desktop window.

**1594 — `tests/hermes_cli/test_kanban_provider_walls.py`:** `test_keep_pattern_card_preserves_strong_route_under_wall` replaces the earlier expectation that a keep-pattern route is retiered.

**1595:**

- `tests/hermes_cli/test_kanban_quota_episode.py::test_new_unpinned_card_created_after_a_known_wall_never_routes_to_it`; 1606 archives the first card to isolate dispatch of the new one.
- `tests/hermes_cli/test_kanban_worker_exit_trailer.py::test_fresh_process_sweep_books_the_logged_exit_code` separately asserts episode count for the quota exit code.

**1606 — `tests/hermes_cli/test_kanban_quota_episode_walls.py`:**

- `test_episode_default_card_without_class_alternate_falls_back_to_strong_tier`
- `test_episode_fallback_never_reroutes_an_explicit_pin`
- `test_episode_never_admits_a_target_that_is_walled_elsewhere` (provider file and live-pool probe)
- `test_episode_still_admits_a_target_with_no_wall_anywhere`
- `test_stuck_deadline_reads_the_episode_backoff_not_one_cooldown`
- `test_stuck_deadline_keeps_an_earlier_providers_longer_wall`
- `test_stuck_warning_names_the_episode_deadline_end_to_end`
- `test_expired_episode_still_honours_another_cards_wall`
- `test_repin_onto_a_provider_walled_elsewhere_waits_and_keeps_the_pin`
- `test_review_card_waits_when_its_strong_provider_is_walled_elsewhere`
- `test_class_with_every_route_walled_falls_back_to_strong_tier`

Untested:

- Actual provider allocation/network requests, live messaging transport and Desktop rendering; routing and delivery tests use controlled probes, spawn callbacks or adapters.
- Full worker-turn execution through `gateway/session_kanban.py` to emitted concrete-route receipt; route extraction helpers and constructed receipts are covered instead.
- Dedicated warning cases for infrastructure/owner-restart cooldowns, review-lane census, and deadline-lookup exceptions.
- Strong fallback with a concrete default belonging to multiple classes.
- Explicit assertion that quota notices never wake an origin turn; the patch excludes the event from wake kinds, while delivery tests cover notification collection.

These were not rerun by the mirror pass.

## Follow-up 2716, 2717: another gateway's wall holds only while this pool agrees

- **Behavior (2716):** a hinted quota wall recorded by one gateway came from that gateway's own OAuth pool, but admission held the card on every gateway until the deadline. Now a wall is dropped when this gateway did not just see it (the walled run's claim lock belongs to another host, or this gateway's own wall is older than `LOCAL_WALL_TRUST_SECONDS`, 30 minutes) and this pool has positive room for every walled provider on the concrete model. For a class wall (`class:<name>`) the class's routes for that provider in the assignee's `model_routing` policy are checked. Room means a live OAuth entry with no quota or bench cooldown (`quota_room`, `live_quota_room`): an empty, dead, auth-benched or unreadable pool never lifts a wall. A wall this gateway just saw, and a wall with no recorded provider, still hold. If the run then hits the wall here, its new episode holds it on this gateway.
- **Behavior (2717):** when every wall is lifted this way, a concrete base route still goes through the walled-elsewhere check (provider-file or live-pool walls) and the alternate/hold path, so a card moved off one provider and walled on another elsewhere is not admitted back onto a provider that is walled. Only a class route (allocated against this pool at spawn) is admitted outright.
- **How it works:** `kanban_quota_episode.py` adds `_wall_origin` (recorded time from the stored `quota_episode` event or the run end; local when the run's `claimed` lock matches `is_local_claim`, or no lock exists), `_wall_models` and `_served_here`; `decision()` filters outstanding walls through `_served_here` (non-database errors keep the walls and log a warning, database errors propagate). 2717 computes the held reason before filtering and replaces the early return with a `lifted` flag. `agent/model_routing_unavailable.py` splits the pool scan into `_quota_scan`, with `quota_room` beside `quota_unavailable`; `kanban_provider_walls.live_quota_room` wraps it and returns False for `auto`, class selectors and read errors.
- **Code:** `agent/model_routing_unavailable.py`, `hermes_cli/kanban_provider_walls.py`, `hermes_cli/kanban_quota_episode.py`.
- **Tests:** source coverage, **not run in this mirror**: `tests/hermes_cli/test_kanban_quota_episode_gateway.py` (new) and `tests/agent/test_subscription_quota_exhaustion.py` (`test_quota_room_is_positive_evidence_only`). Entry path: `kanban_db_dispatch.check_respawn_guard` and `dispatch_once` on a temporary board, with the pool room check and provider walls monkeypatched and another host simulated by changing the claim prefix: a wall recorded on one gateway admits on another with room and still holds on the recorder; no room keeps the hold; the own-wall trust window boundary; a class wall checks the concrete route; an unknown-provider wall holds everywhere; `live_quota_room` with an empty or unreadable pool; 2717's provider-file and live-pool walled-codex cases assert only that the walled base route is never spawned. A real credential pool across two gateways and the synced event transport are **untested**.
- **Gotchas:** pools are per gateway, so a wall's deadline is no longer authoritative fleet-wide; the 30-minute trust window applies to this gateway's own walls. The "external walls are not merged into persisted episode deadlines" gotcha below is unchanged.

## Applying

Apply in mirror order: **1592 → 1594 → 1595 → 1606**, with the intervening series and existing Kanban routing/receipt infrastructure. These are incremental patches, not a standalone implementation; 1606 modifies helpers introduced by 1592 and 1594, and 1595 updates an existing exit-trailer test.

No new configuration key is introduced. Episodes use the existing rate-limit cooldown as their backoff base. Class alternates use the assignee's `model_routing` policy and provider configuration. Strong fallback uses existing `kanban.default_model`/`default_provider` and `kanban.strong_model`/`strong_provider` settings. Persisted episode data uses existing task events rather than a new database table.

## Gotchas

- The two-hour cap applies to backoff, not an authoritative longer reset hint. Short hints still cannot shorten backoff.
- Expiration allows another attempt; only a non-wall ended run resets the episode count. Unknown provider custody deliberately prevents speculative reroutes.
- An automatic alternate is not permission to rewrite explicit pins or lower a strong/review tier. A changed pin is admitted only when the decision can establish a different free concrete provider.
- The warning deadline is the latest outstanding **episode** deadline. External walls are checked for admission but are not merged into persisted episode deadlines by this patch; do not describe the suffix as a guaranteed time when all provider capacity returns.
- Clock suppression needs a complete candidate census. A non-clock-held or ineligible candidate can keep the tick eligible for a stuck warning; capacity suppression remains a separate check.
- Optional bookkeeping failures are handled differently from database integrity failures: the latter still propagate, rather than silently committing a partial episode.

## Plugin?

No. The change joins core reaping, attempt receipts, admission/routing, dispatch health accounting and existing notification delivery, which must share the same durable episode decision.
