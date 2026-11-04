# Committed model switches reach the next build

Patches: **1732** (`6f42180d3c`, `[daveey] Restore recorded route when adopting legacy local sessions`); **1733** (`cdb9d4a76a`, `[daveey] Committed model switch replaces the recorded allocation at the next build`); **1734** (`49c12ce781`, `Managed workers honour a committed model switch through owner-scoped receipt operations`); **1735** (`8f4c14f4d7`, `[daveey] Composed regression: committed model switch reaches the next managed worker`); **1736** (`9cfbff688a`, `[daveey] Preserve auto provider for committed class selections`); **1737** (`92e72365f8`, `[daveey] Reuse provider route helper without growing initialization facade`); **1763** (`6a5b35d7e8`, `[daveey] Fence committed route-switch intent replacement in the allocator transaction`); **1765** (`1f5391637e`, `fix(routing): type the committed allocation fence transport [daveey]`); **1766** (`615d439f4a`, `[daveey] fix(routing): honour committed switch intents in commit order, one event each`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Adopting a legacy local session restores its recorded route (1732).
- A committed model switch writes a one-shot route-switch intent; the next build for exactly that session and tuple replaces the recorded allocation and consumes the intent by compare-and-delete (1733). Managed workers read and consume it through owner-scoped receipt operations (1734); a composed regression drives the real switch through a managed worker (1735).
- Class selections keep the auto provider (1736). Intent replacement is fenced in the allocator transaction by a token of the raw intent (1763, typed transport 1765), and intents are honoured in commit order, one event each (1766).

## How it works

`agent/model_routing_switch_intent.py`, `model_routing_agent.prepare_agent_route(replace=...)`, `model_routing_state` switch fences, `RuntimeSessionStore` `route_switch.pending/consume`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1732: (subject only)
- 1733: model_in_transaction moved sessions.model/provider but left model_config.model_routing and the allocation store on the old route, so restore_route resumed the old pin or the next build refused with 'Exact pinned route cannot fail over'. The receipt transaction now clears the stale record and writes a one-shot state_meta intent (gateway.route_switch.v1.<target>); prepare_agent_route honours it only for exactly that session and tuple (replace=True), consuming it by compare-and-delete after the allocation commits. Rejected/deferred preparations never write it; preparation's route lookups run a...
- 1734: The production worker's RuntimeSessionStore had no get_meta/_write_sql, so pending() silently returned None and every worker rebuild after a switch hit 'Exact pinned route cannot fail over'. Intent read/consume are now narrow worker.persist operations (route_switch.pending/consume) bound to the worker's own assigned session and generation by the receipt fence; SessionDB exposes the same two methods. Consume is compare-and-delete. An auto build honouring an intent resolves on the switch's committed provider. The model receipt row writer now moves provider/base_url/api_mode/ gateway_runtime a...
- 1735: Drives the canonical session.mutate model switch (real prepare_model/switch_model/_commit_policy/ model_in_transaction) on an isolated HERMES_HOME, then managed_worker.execute from the owner's _bootstrap frame with a real RuntimeSessionStore over worker.persist, real worker_route/route_runtime and a real AIAgent: exact claude-opus-5-5 allocation -> committed gpt-6-astra switch -> worker builds Astra on openai-codex and consumes the intent once. Controls: a revision-conflict refusal and a deferred pick (while the turn runs) keep the worker and allocation on Opus; the deferred pick reaches th...
- 1736: (subject only)
- 1737: (subject only)
- 1763: A cold /review or /refine can build a same-session AIAgent while the durable queue drains the next turn; the adapter idle guard reserves nothing. Both builds read the same committed intent, both allocated with replace=True (two round-robin choices, possibly two providers), and only one compare-delete won.
- 1765: (subject only)
- 1766: Why-chain (five whys, evidence: deleg_d342c5fe review probe4.log at 24096f5d558): 1. Next exact build on the newer pick raised "Exact pinned route cannot fail over": the durable allocation held the OLDER pick's tuple. 2. A build that read intent A allocated after a newer intent B was already honoured; fence(A) != fence(B), so the allocator replaced again with A's tuple. 3. The switch_fences row was an equality fence (last token per root/class) with no order, and the read -> allocate window is not serialized against later switches (cold /review vs the drained queued turn). 4. Intent rows had...

## Code

`agent/agent_init.py`, `agent/model_routing.py`, `agent/model_routing_agent.py`, `agent/model_routing_managed.py`, `agent/model_routing_runtime.py`, `agent/model_routing_state.py`, `agent/model_routing_switch_intent.py`, `agent/runtime_session_store.py`, `gateway/session_local_migration.py`, `gateway/session_mutation_model.py`, `gateway/session_worker_construct.py`, `hermes_cli/runtime_provider.py`, `hermes_state.py`, `hermes_state_mutation_prepared.py`, `hermes_state_worker_lifecycle.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_route_switch_intent.py`: `test_an_intent_for_another_session_or_tuple_does_not_move_the_pin`, `test_committed_switch_survives_restart_and_replaces_the_exact_pin`, `test_failed_allocation_rolls_back_and_keeps_the_intent`, `test_managed_worker_build_with_a_non_matching_intent_keeps_the_pin`, `test_managed_worker_consume_is_compare_and_delete`, `test_managed_worker_failed_allocation_keeps_the_intent`, `test_managed_worker_store_honors_committed_switch`, `test_managed_worker_store_refuses_another_sessions_intent`, `test_model_switch_row_writer_moves_the_whole_route_and_billing`, `test_no_intent_cannot_override_an_exact_pin`, `test_route_runtime_prebuild_lookup_records_no_allocation`
- `tests/agent/test_route_switch_intent_fence.py`: `test_a_newer_committed_intent_replaces_again`, `test_auth_failure_rolls_back_the_fence_and_a_retry_replaces`, `test_composed_same_session_builds_honour_one_intent_once`, `test_concurrent_auth_failure_leaves_the_other_build_free_to_replace`, `test_concurrent_class_intent_cannot_split_providers`, `test_crash_between_allocation_and_consume_retries_without_a_second_replacement`, `test_failover_after_honoured_intent_is_not_undone_by_a_lingering_intent`, `test_ordinary_pin_without_intent_still_refuses`, `test_unfenced_replace_stays_unconditional`
- `tests/agent/test_route_switch_intent_order.py`: `test_identical_intents_are_distinct_ordered_events`, `test_repeated_identical_intent_after_an_unfenced_switch_replaces_again`, `test_rollback_then_newer_intent_still_replaces_in_order`, `test_stale_older_build_after_a_newer_switch_cannot_replace_or_wedge`, `test_unordered_and_cross_store_tokens_keep_the_equality_fence`
- `tests/gateway/test_legacy_launch_recorded_route.py`: `test_legacy_launch_keeps_recorded_exact_pin`, `test_legacy_launch_retains_matching_route_endpoint`
- `tests/gateway/test_model_mutation_route_intent.py`: `test_committed_pick_records_the_intent_and_drops_the_stale_record`, `test_deferred_pick_writes_its_intent_only_at_the_turn_boundary`, `test_optimistic_rejection_changes_no_route_state`, `test_preparation_route_lookups_never_allocate`
- `tests/gateway/test_model_switch_worker_composed.py`: `test_class_switch_releases_exact_pin_and_rebuilds`, `test_committed_switch_reaches_the_next_managed_worker`, `test_deferred_switch_keeps_the_original_until_the_turn_boundary`, `test_rejected_switch_keeps_the_worker_on_the_original_pin`

Untested by their own patch (refactors, constants or CI routing): 1737, 1765.

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- Rejected or deferred preparations never write an intent.

## Plugin?

No: these change core modules that plugins cannot hook.
