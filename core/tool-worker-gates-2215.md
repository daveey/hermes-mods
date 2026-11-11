# Extract tool worker gates without changing dispatch behavior

Patch **2205**.

## Behavior

This is a structural refactor: authorization serialization, start ordering, worker registration and interrupt fan-out retain their existing behavior. Imports from `agent.tool_executor` remain available, and log records keep the `agent.tool_executor` logger name.

## How it works

The worker-thread primitives move to `agent/tool_worker_gates.py` and are re-exported by the executor. The module lazily resolves `run_agent` so patches to its interrupt setter remain effective. The authorization gate still derives its lock timeout from the human-wait ceiling, excludes actual approval waits rather than arbitrary gate residency, and falls back to unserialized prompting on lock timeout. The start-order gate still releases abandoned workers and bounds ordering waits. Registration cleanup clears the interrupt bit when a worker exits.

The purpose is code-health line-budget headroom, not a new scheduling policy.

## Code

- `agent/tool_worker_gates.py`: `_ConcurrentToolAuthorizationGate`, `_StartOrderGate`, `_WorkerStartOnce`, `_BatchAbandoned`, timeout helper and worker-thread helpers.
- `agent/tool_executor.py`: imports/re-exports and unchanged dispatch callers.

## Tests

**No test files are added or changed by 2205.** This documentation/mirror task did not execute the source regression suite. The re-export compatibility and unchanged logger can be inspected in the supplied patch, but this is not a runtime test result; concurrency and interrupt behavior remain unverified by this task.

## Applying

Use only the complete ordered replay recipe in [README.md](README.md), including bridges and verification. Do not apply this refactor independently from its source context.

## Gotchas

Callers retain the old import path, but definitions now live in the extracted module; patching a re-exported module attribute is not necessarily patching the defining module's global. Preserve the lazy `run_agent` lookup and original logger when making follow-up changes.

## plugin?

No. This moves internal executor implementation into a core module; it does not introduce a plugin extension point.
