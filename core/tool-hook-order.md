# transform_tool_result runs after post_tool_call on every tool path

Patch:

- `patches/0137-fix-agent-fire-transform_tool_result-after-post_tool.patch`: backend
  (`agent/tool_executor.py`, `model_tools.py`, new test `tests/agent/test_tool_hook_order.py`).

What you see: a plugin that queues a hint in `post_tool_call` and attaches it in `transform_tool_result` now gets
the hint onto the SAME tool result. Before, on sequential registry tools (`terminal`, `read_file`, ...) it landed on
the next tool's result, one call late (e.g. the coach plugin's live-checkout hint showed up on a later read-only call).

## Behavior

- For every executor path (sequential and concurrent, registry and inline tools), the hooks for one call fire in
  the order `post_tool_call` → `transform_tool_result`, as `_apply_transform_tool_result_hook` documents ("Runs
  after post_tool_call").
- Results, hook arguments and the concurrent/inline paths are otherwise unchanged; those paths already had the
  right order.

## How it works

- Root cause: the sequential executor wraps `handle_function_call` in `model_tools.suppress_post_tool_call_hook()`
  and emits `post_tool_call` itself afterwards in `_publish_sequential_result`, but `handle_function_call` still
  ran `transform_tool_result` inline, i.e. before post.
- `model_tools` gains `defer_transform_tool_result_hook()`: inside it, `_apply_transform_tool_result_hook` queues
  the call instead of running it; `apply_deferred_transform_tool_result()` runs a queued entry.
  `suppress_post_tool_call_hook(False)` (connector batch entries that own their own post) lifts the deferral, so
  those still transform inline.
- In `agent/tool_executor.py`, `_SequentialDispatch.transform_applied` becomes `deferred_transforms`, and
  `_publish_sequential_result` runs them right after `emit_post`.

## Tests

- `tests/agent/test_tool_hook_order.py`: a fake plugin records hook order and attaches its queued hint, run over
  sequential/concurrent × registry/inline. It asserts `[post, transform]` per call id and the hint on that call's
  own result. Before the fix, only sequential-registry failed (`[transform, post]`); all 4 pass after.
- `tests/agent/test_run_agent.py -k "execute_tool or sequential or concurrent or hook or transform"`: 35 pass;
  `test_sequential_tool_calls_run_without_delay` fails the same way on the pre-fix base (background threads hit
  the globally patched `time.sleep`), so it is not caused by this change.

## Applying

Core-only; no config. Takes effect in new backends on the new core.

## Gotchas

- The coach plugin's transform-side `check_live` workaround stays; it is idempotent with this fix.

## Plugin?

No: the order is decided inside the executor, before any plugin hook runs.
