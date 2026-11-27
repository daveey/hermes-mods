# Kanban canonical home, execution separation and explicit-home readers

Patches: 6141–6156 (16 entries).

## Behavior

- Card administrative home is separated from execution placement: a managed worker can create on the canonical home while execution labels remain; dynamic worker slots inherit the opted-in pool policy.
- Canonical transport preserves explicit caller intent (empty runs_on/requires/skills, zero priority, string label normalization, sender tenant) and is derived from the public schema; owner-side validation keeps legacy envelopes valid and optional null means omission.
- Landing preserves canonical contracts (any-machine leases, retry/hold fields) through hub admission.
- Board readers accept an explicit gateway/profile scope and never consult ambient process-home state for explicit-home entry points; owner and replica graph observation is confined to supplied snapshots.

## How it works

kanban_creation_home, kanban_creation_contract, kanban_placement, kanban_origin_owner and kanban_origin_forward_decision implement transport; kanban_chat_links, kanban_origin_ledger/local and kanban_session_activity take scope arguments.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_creation_contract.py`
- `hermes_cli/kanban_creation_home.py`
- `hermes_cli/kanban_execution_observations.py`
- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/kanban_origin_forward_decision.py`
- `hermes_cli/kanban_origin_ledger.py`
- `hermes_cli/kanban_origin_local.py`
- `hermes_cli/kanban_origin_owner.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_replica_upstream.py`
- `hermes_cli/kanban_session_activity.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_placement.py`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `tests/fixtures/kanban_origin_envelopes.json`
- `tests/hermes_cli/test_creation_home_transport.py`
- `tests/hermes_cli/test_execution_observation_source_safety.py`
- `tests/hermes_cli/test_explicit_home_reader_args.py`
- `tests/hermes_cli/test_kanban_creation_contract.py`
- `tests/hermes_cli/test_kanban_home_marker_nits.py`
- `tests/hermes_cli/test_kanban_origin_envelope_corpus.py`
- `tests/hermes_cli/test_kanban_pool_queue.py`
- `tests/hermes_cli/test_origin_explicit_home.py`
- `tests/hermes_cli/test_replica_explicit_acquisition.py`
- `tests/hermes_cli/test_review_4255_scope.py`
- `tests/hermes_cli/test_review_explicit_local_deep.py`
- `tests/hermes_cli/test_review_home_selfwork_compat.py`
- `tests/tools/test_kanban_home_field_contract.py`
- `tests/tools/test_kanban_home_landing_contract.py`
- `tests/tools/test_kanban_home_schema_contract.py`
- `tests/tools/test_kanban_home_schema_empty.py`
- `tests/tools/test_kanban_home_schema_omitted.py`
- `tests/tools/test_kanban_home_schema_set.py`
- `tests/tools/test_kanban_home_tenant.py`
- `tests/tools/test_kanban_worker_canonical_home.py`
- `tests/tools/test_review_explicit_empty_home.py`
- `tests/tools/test_review_home_immediate_ask.py`
- `tests/tools/test_review_home_normalization.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- No migration or rollout is implied.
- Omitted keyword arguments keep the legacy process-home path.
- tools/kanban_tools.py sits at a size cap, so one entry is a pure headroom refactor.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban creation, placement and origin readers inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
