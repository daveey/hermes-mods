# Kanban: attached source hints reach managed worker exec and code kernels

Mirror entries 5135, 5136.

## Behavior

- When a card has an attached local source checkout, the managed worker child (a separate gateway child process) now receives only `SOURCE_PATH`, `SOURCE_COMMIT` and `SOURCE_BRANCH`, rebuilt from the current attached journal at the real spawn boundary. Previously the dispatcher's `source_env` only reached the submit client and never the child.
- Task, run and claim are validated; a wrong task/claim, terminal run, unreadable source database or pending/aborted attachment refuses before spawn.
- Unattached scratch cards clear any stale inherited source hints.
- The code-execution kernel's exact environment allowlist now admits those three non-secret names, and nothing else (no board, claim or credential variable).
- Ordinary `env=None` / kwargs semantics for non-kanban spawns are unchanged, and the gateway's own environment is copied, never mutated.
- 5136 updates a test fixture for the new fail-closed boundary: the lost-worker fixture now carries a complete db/task/run/claim identity.

## How it works

- `gateway/session_kanban_source_env.py` builds the hints from the journal; `gateway/session_kanban_scope.py` calls it at the child's Popen boundary.
- `tools/code_execution_env.py` adds the three names to the kernel allowlist.
- `tests/gateway/fixtures/kanban_source_env_probe.py` is a child-side probe that prints what startup, terminal and the kernel actually see.

## Code

- `gateway/session_kanban_scope.py`
- `gateway/session_kanban_source_env.py`
- `tools/code_execution_env.py`

## Tests

Source tests (not executed in this mirror pass):

- `tests/gateway/test_kanban_source_environment.py`: `test_attached_source_reaches_actual_startup_and_both_tools`, `test_unattached_scratch_strips_inherited_source`, `test_invalid_source_claim_refuses_before_spawn`, `test_source_env_copies_ambient_and_does_not_mutate_gateway`, `test_non_kanban_env_unchanged`, `test_unreadable_source_database_refuses_before_spawn`.
- `tests/gateway/test_managed_worker_unknown_status.py` (5136): the loss/cancellation-order fixture with a complete claim.

Windows or non-POSIX child spawning is untested.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- The hints are non-secret but are still scoped: only these three names are added to the kernel allowlist.
- Fixtures that fabricate a partial policy (db/task only) now fail closed; give them a full run/claim.

## Why core instead of a plugin

The env is assembled where the gateway spawns the worker child and where the sandboxed kernel filters its environment; both are core launch paths with no plugin hook.
