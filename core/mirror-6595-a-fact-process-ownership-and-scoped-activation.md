# Fact runtime: shared process owner, lazy scoped activation and watch rearm

Patches: 5988, 5989, 5990, 5991, 5992, 5993, 6047, 6048, 6049, 6073.

## Behavior

- One process ChangeFeed and FactStore serve all registrations, with per-registration context and budgets; discovered but unused boards/profiles are inert until consumer demand queues activation, off request paths.
- Native watches rearm only on coverage loss (overflow, unmount, lost watches, structural ancestor changes), not on ordinary modification events.
- Admission is rerouted after a timed-out terminal drain and dormant admission is retained after a delivery drain refusal.
- View owners bind content sources clock-only, not their rows.
- Annotation refresh blocks on a stop event during coalesced waits instead of polling the descriptor.

## How it works

fact_process_owner, fact_scope_activation, fact_inprocess, fact_source_watch, fact_view_owner and annotation_event_loop implement ownership and watch.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/annotation_event_loop.py`
- `hermes_cli/annotation_file_events.py`
- `hermes_cli/fact_annotation_boot.py`
- `hermes_cli/fact_annotation_runtime.py`
- `hermes_cli/fact_annotation_sources.py`
- `hermes_cli/fact_board_inputs.py`
- `hermes_cli/fact_board_startup.py`
- `hermes_cli/fact_capture_queue.py`
- `hermes_cli/fact_content_owner.py`
- `hermes_cli/fact_inprocess.py`
- `hermes_cli/fact_links_startup.py`
- `hermes_cli/fact_process.py`
- `hermes_cli/fact_process_owner.py`
- `hermes_cli/fact_scope_activation.py`
- `hermes_cli/fact_source_commit.py`
- `hermes_cli/fact_source_watch.py`
- `hermes_cli/fact_tree_startup.py`
- `hermes_cli/fact_view_owner.py`
- `hermes_cli/sessions_native_startup.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_annotation_coalesce_mitigation.py`
- `tests/hermes_cli/test_annotation_event_loop_rearm.py`
- `tests/hermes_cli/test_annotation_file_events.py`
- `tests/hermes_cli/test_annotation_lazy_profiles.py`
- `tests/hermes_cli/test_annotation_prepare_inert.py`
- `tests/hermes_cli/test_annotation_prepare_lifespan.py`
- `tests/hermes_cli/test_fact_board_lazy.py`
- `tests/hermes_cli/test_fact_capture_queue.py`
- `tests/hermes_cli/test_fact_clock_only.py`
- `tests/hermes_cli/test_fact_content_owner.py`
- `tests/hermes_cli/test_fact_links_startup.py`
- `tests/hermes_cli/test_fact_process.py`
- `tests/hermes_cli/test_fact_process_owner.py`
- `tests/hermes_cli/test_fact_process_views.py`
- `tests/hermes_cli/test_fact_scope_activation.py`
- `tests/hermes_cli/test_fact_shared_delivery.py`
- `tests/hermes_cli/test_fact_shared_watch.py`
- `tests/hermes_cli/test_native_watch_rearm.py`
- `tests/hermes_cli/test_review_delivery_readmission.py`
- `tests/hermes_cli/test_review_enqueue_wakeup.py`
- `tests/hermes_cli/test_review_expiry_enqueue_lock.py`
- `tests/hermes_cli/test_review_watch_drain_admission.py`
- `tests/hermes_cli/test_sessions_cold_bootstrap.py`
- `tests/plugins/test_board_demand_startup.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Capture limits are unchanged.
- Tests check serialization across scopes and cold-bootstrap lease custody.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the fact runtime lifecycle inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
