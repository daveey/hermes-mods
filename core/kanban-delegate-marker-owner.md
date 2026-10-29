# Delegate-child env marker is bound to its owner process

Patches:

- `patches/0208-fix-kanban-bind-the-delegate-child-env-marker-to-its.patch`: `HERMES_DELEGATED_CHILD_OWNER` binding, orphans heal.

## Behavior

Every kanban worker subprocess carries `HERMES_DELEGATED_CHILD_CONTEXT` by design. Env vars are inherited by every
descendant, including processes that leave the lineage (`open`, detached launches, ppid 1), so a Hermes.app reopened by
a worker's `hermes update` fenced every Desktop chat as a delegate child and its Kanban writes were refused.

Now `scrub_kanban_env` stamps `HERMES_DELEGATED_CHILD_OWNER="<pid>:<start fingerprint>"` of the agent process that first
fences a lineage (an inherited owner is kept). The env marker counts only while that exact process incarnation is alive
AND an ancestor of the reader (result cached 10 s). A process outside the lineage drops the agent-scope env family from
its own env once (logged), so neither it nor its children are fenced. An unbound legacy marker (no owner) still fences
(fail closed). The in-process `delegate_task` contextvar is unchanged. Launch-site scrubbing (see
`top-level-env-scrub.md`) stays as defence in depth.

Trade-off: a worker's background process orphaned after the worker exits is no longer fenced; it has no live owner to
protect.

## How it works

`is_delegated_child_process_context()` used to trust the variable alone. It now verifies the owner pid + start-time
fingerprint is alive and in the reader's ancestry; if not, it scrubs the family from `os.environ` and returns False.

## Code

- `agent/delegation_context.py` (`scrub_kanban_env`, `is_delegated_child_process_context`, owner check).
- `apps/desktop/electron/leaked-worker-env.ts`: the new variable joins the leaked-env list.

## Tests

`tests/agent/test_delegate_marker_owner_binding.py` (86 lines): owner binding, dead owner, non-ancestor reader,
legacy unbound marker. Not re-run when this doc was written (untested here).

## Applying

`git am -3 --empty=keep` in order, after 0169, 0170, 0171, 0172.

## Gotchas

- The fingerprint is process start time; pid reuse alone cannot revive a dead owner.
- Hermes.app processes started before the fix still carry the old marker with no owner and stay fenced until restarted.

## Plugin?

No. The fence is checked inside core Kanban tools.
