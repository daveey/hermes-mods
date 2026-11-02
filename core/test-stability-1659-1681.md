# Test stability and resource use in patches 1659–1681

Test-focused patches **1660**, **1664**, **1668**, **1669**, **1679**, **1681**. Restart-fixture patch **1676** is documented with its production feature in [gateway restart](gateway-restart-drain-holds-and-reattach.md). These changes do not add runtime configuration defaults or plugins.

## Behavior

- **1660** (`dd2aea7d8991e784a55a0c41c853a745d79a881a`): bind-guard tests let the actual listener allocate port 0 and inspect its bound address, instead of releasing a speculative free port before use. Cleanup covers partial setup.
- **1664** (`01440842d435ad9e30f9bab1b9a157f4b2284a1b`): the idle-watcher tests explicitly alias a shared fixture from its module, avoiding imported-name shadowing without changing coverage.
- **1668** (`1f5656e76b5ac2a35c58f42def47b00e0889ec00`): an unreachable-backend fixture retains a bound, non-listening socket for the whole fanout assertion, preventing another test from turning expected refusal into success.
- **1669** (`bf071a2bd5def5e02dfd8ef8fbf1ce921852535c`): optional aiohttp/Anthropic transport tests skip explicitly when their SDK is absent. Real cgroup placement cases first probe kernel migration permission with a disposable child; permission/read-only failures skip, unrelated errors still fail.
- **1679** (`584909e94d12c6ced38777fa43f3b4f65a5b0702`): billing E2E fixtures set `auto_decompose_min_age_seconds: 0`, so the test measures billing rather than waiting for the production creator-parking grace.
- **1681** (`198a1a79d20502a76ab8e383fa6cc1351cabc3e1`): the sidecar-key registry scan parses one Python file at a time rather than retaining every AST. It keeps reduced write records and constants while preserving the unresolved-key accounting contract.

## How it works

The two socket changes preserve ownership until the relevant assertion or deliberate rebind. The cgroup probe writes only the disposable child's PID to `cgroup.procs` and always reaps it; it does not invoke the production migration helper, so a production regression cannot masquerade as missing host capability. Only EACCES, EPERM and EROFS trigger skips. The cancellation test requests migration permission only for its late-cancel branch.

The AST scanner's `_key_ref()` reduces literals to string references, names/attributes to identifier references, and unsupported nodes to unresolved references. `_collect()` retains module constants and `(file, location, references)` records, releases each tree, then resolves keys using local/global constants. Owner-only writers remain excluded from write scanning; nonliteral dictionaries and spreads remain unresolved.

## Code

Only source test files are changed by the patches listed above:

- `tests/gateway/test_api_server_bind_guard.py` (1660)
- `tests/tui_gateway/test_idle_watcher_gates.py` (1664)
- `tests/hermes_cli/test_plugin_activation_serve_fanout.py` (1668)
- `tests/gateway/test_kanban_notifier_served_apiserver_wake.py`, `test_kanban_wake_acceptance.py`, `test_managed_kanban_scope_placement.py`, plus `tests/tui_gateway/test_class_routing.py` (1669)
- `tests/e2e/core/kanban/test_kanban_decompose_billing.py` (1679)
- `tests/hermes_state/test_sidecar_keys_registry.py` (1681)

## Tests

These are the actual source test targets changed or named by the patch, **not test results from this mirror**:

| Patch | Source coverage |
|---|---|
| 1660 | `tests/gateway/test_api_server_bind_guard.py::TestBindMechanics::test_immediate_rebind_after_disconnect`; the same class's occupied-port case also switches to the bound listener. Its function name is outside the supplied diff context. |
| 1664 | `tests/tui_gateway/test_idle_watcher_gates.py::test_idle_directory_not_relisted_but_member_edit_seen` uses the exported `watcher_home` alias; the shared fixture supports the rest of that module too. |
| 1668 | `tests/hermes_cli/test_plugin_activation_serve_fanout.py::test_unreachable_backend_is_named_and_the_other_still_activates` |
| 1669 | `test_kanban_notifier_served_apiserver_wake.py::test_served_profile_wake_runs_in_process_only_for_the_session_it_owns`, `test_internal_session_turn_targets_the_live_session_under_the_owner_profile`; `test_kanban_wake_acceptance.py::setup_route` gates the Raft branch; `test_class_routing.py::native_transport` and `test_desktop_exhaustion_cannot_enter_paid_fallback` gate Anthropic imports. |
| 1669 | `tests/gateway/test_managed_kanban_scope_placement.py::test_migration_probe_classifies_kernel_errors_and_reaps` checks success, permission failures and EIO; `test_child_and_descendant_inherit_run_scope_and_limit`, `test_cancel_interleaving_leaves_no_child`, `test_spawn_registered_worker_places_real_child` consume the capability fixture. |
| 1679 | `tests/e2e/core/kanban/test_kanban_decompose_billing.py` shared `GATEWAY_CONFIG`; the two-line patch does not add or expose individual test names. |
| 1681 | `tests/hermes_state/test_sidecar_keys_registry.py::test_every_model_config_writer_key_is_a_worker_sidecar_key` (named in the source commit message) uses the refactored collector. No new equivalence test is added in this patch. |

None were rerun for this documentation task. Patch 1681 reports identical `(found, unresolved)` results and lower peak memory/time in its source message; those measurements were not reproduced here. Optional dependency or cgroup skips are not evidence that the skipped behavior passed.

## Applying

Apply the numbered patches in order with the [README replay procedure](README.md). Patch 1664 follows the new 1663 idle-watcher tests; 1679 assumes the existing billing suite; 1681 assumes the existing sidecar registry test. Do not apply a fixture fix without the tests it changes.

## Gotchas

A bound socket is not a listening server. Releasing a free-port probe before use restores the race. Missing cgroup migration authority is distinct from broken production placement; probe the kernel independently and never move the test runner itself. The billing grace override is test-only. The streamed AST implementation must retain unresolved references and cross-file constant lookup, not simply discard unsupported writes to reduce memory.
