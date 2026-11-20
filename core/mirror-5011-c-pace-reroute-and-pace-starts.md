# Pace reroute, review-hold fallback, and pace_starts default

Patches: **4556–4562, 4600, 4602–4604** (5011 snapshot).

## Behavior

When every candidate pool of a card's route is ahead of pace, the quota governor can start an unpinned default or strong-class card on another serving route of the same class instead of holding it. Mandatory pins, model/provider overrides, require_model cards, and routes the tier resolver did not choose still wait.

A class card is judged on the governed subset of its candidates. One pool at its pace limit is enough to move a class card off that pool, because class allocation is not pace-aware. Peers this host cannot serve are skipped. A lapsed or mismatched pace offer refuses the launch (requeue, no failure penalty) instead of starting on the held route.

A far-held review route, including an explicit `review_model` override, moves to the next independent reviewer that is neither held nor walled and that this host can serve. The implementer's model is never used. Review receipts record the held provider and reset when the route moved.

By default (`kanban.llm_pacing.pace_starts` false) a burn rate does not hold card starts: `admit_limit` limits only a held pool (no account under reserve). Pace is still read for reroute, notices, and runway. Read-only views describe a paced pool as a hold only when `pace_starts` is true.

## How it works

`kanban_llm_pacing_dispatch._pace_alternate` probes the same non-allocating `alternate_route` the quota-wall path uses, with holding pools as extra walls. `kanban_pace_reroute` is a process-local offer/take (TTL 600 s, taken once, bound to the judged route). `apply_model_tier` persists the move on `run_route` before owner launch; receipt failure refuses spawn. `take()` distinguishes no offer from `OfferLost`. `hold()` withdraws a previous unused offer.

`kanban_paced_reroute._probe` requires `live_pool_serving`. Review fallback uses `independent_routes(implementer)` plus the routing registry.

`pace_starts` default false; tests that pin proportional limits opt in. Pacing tests read BOM-tolerant files.

## Code

- `hermes_cli/kanban_llm_pacing.py`
- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_pace_reroute.py`
- `hermes_cli/kanban_paced_reroute.py`
- `hermes_cli/kanban_pacing_defaults.py`
- `hermes_cli/kanban_review_model.py`
- `hermes_cli/kanban_source_launch.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_pace_reroute.py`
- `tests/hermes_cli/test_kanban_model_tier_paced_reroute.py`
- `tests/hermes_cli/test_kanban_review_paced_reroute.py`
- `tests/hermes_cli/test_kanban_llm_pacing.py`
- `tests/hermes_cli/test_kanban_llm_pacing_empty_windows.py`
- `tests/hermes_cli/test_kanban_llm_pacing_followups.py`
- `tests/hermes_cli/test_kanban_llm_pacing_missing_reset.py`
- `tests/hermes_cli/test_kanban_llm_pacing_persistence.py`
- `tests/hermes_cli/test_kanban_pacing_recovery.py`
- `tests/hermes_cli/test_kanban_edit_priority_paced.py`
- `tests/hermes_cli/test_kanban_notice_pacing_admission.py`
- `tests/hermes_cli/test_kanban_pacing_default_reads.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

An ungoverned pool in the class does not exempt governed candidates. A profile-fallback or tier-off route keeps the hold. Offer TTL expiry during workspace prepare must fail closed. Without a free independent reviewer, a review stays on its route and the governor holds it; never the implementer. Notices and priority-raise guards must not call a paced pool a hold unless `pace_starts` is on. Historical CPU/slot counts in commit messages are not measurements from this pass.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
