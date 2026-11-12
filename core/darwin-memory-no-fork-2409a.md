# Native Darwin memory metrics without periodic subprocesses

Mirror entries: [2265](patches/2265-perf-metrics-read-Darwin-memory-without-periodic-subprocesses-daveey.patch), [2266](patches/2266-test-metrics-run-Darwin-dispatch-and-native-ABI-smoke-on-macOS-daveey.patch), [2267](patches/2267-ci-metrics-exercise-native-memory-regressions-on-Darwin-daveey.patch).

## Behavior

- Read fresh Darwin memory counters through libSystem rather than spawning periodic vm_stat/sysctl subprocesses from the gateway.
- Preserve the previous available-memory policy and omit the metric when the native API is unavailable or incompatible.
- Route ABI and no-fork regressions to a dedicated macOS workflow.

## How it works

machine_memory_darwin caches ctypes bindings, not counter values. It reads hw.memsize, host page size and host_statistics64, computes available pages from free + inactive + purgeable, clamps the percentage and deallocates the host port in finally. The old vm_stat display subtracts speculative from free, so this keeps the earlier free/inactive/speculative/purgeable policy without double counting. Native dispatch and real ABI smoke coverage are added in 2266; 2267 adds the path-filtered self-hosted macOS job and workflow-contract test.

## Code

- `hermes_cli/machine_memory_darwin.py`
- `hermes_cli/machine_metrics.py`
- `.github/workflows/native-memory-macos.yml`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_machine_memory_darwin.py` — `test_native_abi`, `test_same_input_policy_freshness_and_cleanup`, `test_fail_closed_and_no_port_leak`, `test_old_kernel_prefix_is_sufficient` (additional cases in this file).
- `tests/hermes_cli/test_machine_memory_nofork.py` — `test_darwin_reader_is_fresh_without_subprocess`, `test_darwin_reader_failure_omits_metric_without_spawn`, `test_real_native_binding_without_subprocess`.
- `tests/ci/test_native_memory_workflow.py` — `test_native_memory_lane_routes_and_executes_real_tests`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Linux fixture tests cannot establish Darwin ABI compatibility. No native macOS smoke or workflow was rerun in this mirror documentation pass. Read failures omit the metric instead of falling back to another subprocess. Host-counter page size, not a translated process's map page size, is required.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
