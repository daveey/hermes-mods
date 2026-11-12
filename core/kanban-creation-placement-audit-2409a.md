# Authoritative creation-time placement audit

Mirror entries: [2276](patches/2276-daveey-Record-final-creation-time-placement-policy-in-task-events.patch), [2287](patches/2287-daveey-Audit-creation-portability-independently-of-worker-pool-enrollment.patch).

## Behavior

- Every created event snapshots the final insertion-time placement_policy, including an explicit null, after caller diagnostic fields are expanded.
- A caller's placement_audit cannot override the authoritative placement_policy; later assignment changes do not rewrite the creation snapshot.
- Record structural portable_reason even when the caller profile has no worker_pools. A null reason means structurally portable, not enrolled or admitted; audit errors explicitly say audit failed.

## How it works

2276 (source PR #1112) adds a required placement_policy keyword to record_created and passes the final value from kanban_db.create_task. The serialized event expands placement_audit first, then writes placement_policy last. Caller audit at source commit 1d4a810f623fbee38ce57709a69f9d26edb4c9e2 found the creation helper's sole production caller in hermes_cli/kanban_db.py; unrelated skill-usage functions sharing the name are not this API. 2287 removes the early no-pool return from creation_audit, retains the caller's placement configuration and emits an explicit audit-failed reason on exceptions. Its no-pool regression crosses DB, CLI and tool front doors against a shared default board while verifying policy remains null.

## Code

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_portable_reason_audit.py` — `test_db_policy_is_creation_time_not_later_state`, `test_runtime_pin_does_not_disable_cloneable_implementation`, `test_created_event_records_audit_when_no_pool_is_configured`.
- `tests/tools/test_kanban_creation_audit.py`
- `tests/hermes_cli/test_kanban_no_pool_audit.py` — `test_no_pool_profile_audits_shared_default_board`, `test_audit_failure_is_visible_not_portable`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Do not interpret portable_reason=null as permission to execute remotely, or placement_policy=null as a missing field. Profile scheduling opt-out must not hide structural diagnostics or borrow the root profile's enrollment. The source helper comment in 2276 predates 2287's unconditional audit; the combined behavior is documented here. No deployment, scheduling replay or live board mutation was performed.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
