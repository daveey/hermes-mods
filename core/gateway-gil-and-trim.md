# Gateway GIL starvation: 1 ms switch interval and young-gen trim

Patches: 1334, 1335.

## Behavior

Under heavy load the gateway's API loop thread spent most samples waiting for the GIL, and Desktop reads on the local connection missed their 4 s timeout (a 2 ms-CPU status call took up to 5.45 s).

- 1335: the gateway sets `sys.setswitchinterval(0.001)` at start (default 5 ms). Measured with an echo loop beside burner threads: p50 222 to 36 ms with 1 burner, 750 to 148 ms with 4. `HERMES_GATEWAY_SWITCH_INTERVAL_MS` overrides it (value in ms, may be fractional); `0`, negative or unparsable keeps the interpreter's current interval; blank means the 1 ms default.
- 1334: `trim_memory` runs a full `gc.collect()` only when a page-release primitive (glibc `malloc_trim`) follows. Otherwise (macOS, musl) it runs `gc.collect(1)`. A full collection holds the GIL for the whole heap walk (2-3 s for ~6M objects) and agent closes force a trim every 5 s, for about 3% footprint gain.

## Code

`gateway/gil_budget.py` (new), `gateway/run_bootstrap.py` (`start_gateway` calls `apply_gateway_switch_interval()` before building the runner), `hermes_cli/mem_trim.py` (`_NO_RELEASE_GC_GENERATION = 1`).

## Tests

- `tests/gateway/test_gil_budget.py`: `test_default_is_one_millisecond`, `test_env_override` (parametrized: 2.5, 0.5, 0, -1, fast, blank), `test_apply_sets_the_process_interval`, `test_disabled_override_keeps_the_current_interval`, `test_start_gateway_applies_it_before_serving` (AST check on `start_gateway` source order)
- `tests/hermes_cli/test_mem_trim.py`: `test_gc_collect_runs_even_without_platform_primitive` (now `collect(1)`), `test_no_page_release_never_walks_the_old_generation` (real gc callbacks), `test_page_release_platform_still_runs_full_collection`, `test_success_collects_then_trims`

Untested: the latency gain itself (measured by hand, not asserted) and a running gateway honoring the env var end to end.

## Applying

Apply 1334 and 1335 in either order. Optional env `HERMES_GATEWAY_SWITCH_INTERVAL_MS`.

## Gotchas

A smaller interval costs a few more GIL handoffs per second for CPU-bound threads. MCP startup's discovery window lowers the interval to 0.5 ms and restores whatever is set here. Only the gateway process is affected. No plugin involved.
