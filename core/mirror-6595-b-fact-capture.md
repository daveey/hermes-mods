# Bounded fact capture, degradation and prepared recency

Patch coverage: 6197, 6241, 6243, 6261, 6284, 6285, 6359.

## Behavior

Clock-only content sources and expensive Kanban annotation capture coalesce bursts on the shared feed. Fact unavailability degrades session-list annotation rather than turning otherwise readable lists into server errors. Accepted demand keys are bounded with LRU retirement of already-bound views.

Project-tree capture has its own serialized worker lane and validity/capture bounds, separate from board capture. Cold session-recency acceleration uses an explicitly prepared derived table, not an unbounded index build during owner open.

## How it works

Adapters expose min_interval; each paced edge schedules one trailing pass no sooner than the last start plus that interval. Demand eviction is committed on the feed thread and never evicts an unserved pending demand. Last published facts expire normally.

Capture budgets start when a job begins, not while queued. Completion wakes pending work only after the saved context exits and the job is retired; closed owners and cancelled jobs drop callbacks. Board and tree lanes remain independent and each lane preserves serialization. Tree input validity is extended without hiding capture failures.

The recency change initially introduces an opt-in range index, then replaces it with a separate message_recency_candidates table maintained by triggers. Explicit hermes_state_recency_build preparation is bounded/resumable with a durable cursor; readers use the legacy exact fallback until the ready marker commits. Message rows remain authoritative through updates, deletes and row-id changes.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/fact_annotation_sources.py`
- `hermes_cli/fact_board_inputs.py`
- `hermes_cli/fact_capture_queue.py`
- `hermes_cli/fact_content_owner.py`
- `hermes_cli/fact_demand_source.py`
- `hermes_cli/fact_feed.py`
- `hermes_cli/fact_process.py`
- `hermes_cli/fact_tree_startup.py`
- `hermes_cli/fact_view_owner.py`
- `hermes_cli/kanban_annotation_facts.py`
- `hermes_cli/web_routers/profiles.py`
- `hermes_cli/web_routers/sessions.py`
- `hermes_state_common.py`
- `hermes_state_recency.py`
- `hermes_state_recency_build.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_fact_feed_pacing.py`
- `tests/hermes_cli/test_fact_content_demand.py`
- `tests/hermes_cli/test_sessions_annotation_degradation.py`
- `tests/hermes_cli/test_fact_capture_lanes.py`
- `tests/hermes_cli/test_fact_capture_completion.py`
- `tests/hermes_state/test_recency_preparation.py`
- `tests/hermes_state/test_recency_build_cli.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Longer validity is a bounded stale-evidence allowance, not freshness proof. Failed captures still invalidate immediately. Recency preparation is opt-in and does not run during database open. This mirror run did not execute preparation, capture benchmarks or service probes.

## Why core, not a plugin?

The shared feed, capture scheduler, database schema and list-route degradation are core infrastructure that plugins consume rather than replace.
