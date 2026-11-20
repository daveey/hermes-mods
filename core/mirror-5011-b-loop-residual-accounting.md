# Residual CPU attribution by thread family and caller

Mirror entries: 4489, 4490, 4491, 4492, 4493, 4494.

## Behavior

- **4489** — Snapshots split unaccounted CPU into live thread-name families and exited threads, with executor, auxiliary-provider, plugin and callback attribution.
- **4490** — Auxiliary-provider scope creation moves to the provider-handoff helper with diagnostic fallback behavior.
- **4491** — Callback counters use cached code-object labels and a lock-free hot path instead of repeated naming and locking.
- **4492** — Executor labels unwrap Context.run submissions to identify the submitted function.
- **4493** — Darwin thread IDs and nanosecond units are corrected, scope flags cover plugin/auxiliary scopes, and accounted copies tolerate concurrent resizing.
- **4494** — The native proc_pidinfo signature explicitly preserves 64-bit Darwin thread identifiers.

## How it works

Residual scopes charge only CPU not already owned by nested passes/scopes, preventing double counting. Snapshot residual fields include family thread counts, CPU, accounted CPU and clamped unaccounted CPU; exited accounted work folds into an exited bucket. When native counters are unavailable, residual availability is explicit rather than invented.

Instrumentation names asyncio/AnyIO executor work as `thread/<qualname>`, protected provider work by its initiating operation, known loaded plugin sites by stable names, and event-loop callback/task-coroutine work as `loop_cb/<name>`. Callback labels are capped at 64 names plus `loop_cb/other`; code-object label caching is separately bounded. Plugin discovery can run again for modules loaded after startup.

`loop_accounting.scopes` and `loop_accounting.callbacks` control the instrumentation. The scope switch also governs auxiliary and plugin sites. On Darwin, PROC_PIDLISTTHREADIDS provides native IDs, thread CPU values are already nanoseconds, and an explicit ctypes uint64 argument prevents truncation. Snapshot copying retries races with lock-free callback bookkeeping.

## Code

- `agent/aux_provider_handoff.py`
- `agent/auxiliary_client.py`
- `hermes_cli/loop_accounting.py`

## Tests

Source residual tests cover family normalization, live/exited residuals, nested passes/scopes, asyncio and AnyIO naming, Context.run, protected auxiliary work, plugin idempotence, callbacks and label caps, and existing snapshot fields. Additional tests cover scopes disabled, native Darwin residuals and a compiled C fixture with IDs exceeding 32 bits. No new CPU burn, compiled fixture or macOS execution was performed here.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_loop_accounting_residual.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Accounted CPU now includes additional scope families, so unaccounted totals are not directly comparable with older snapshots. Best-effort native failures yield unavailable counters. Labels describe operations, not prompt/token contents. The bounded lock-free callback path is instrumentation, not a guarantee of zero measurement overhead.
