# Worker-terminal write refusal names the worker fence

Patches:

- `patches/0963-fix-kanban-worker-terminal-write-refusal-names-the-w.patch`

A worker ran a repair script from its own terminal after two delegate_task reviews and read "delegate_task child contexts cannot mutate Kanban", then filed a lineage-leak investigation. There was no leak: `scrub_kanban_env` gives every worker tool subprocess the `HERMES_DELEGATED_CHILD_CONTEXT` marker with or without delegation.

## Behavior

The env-inherited refusal now names the worker-terminal fence, the owning agent pid and card, and the authorized paths (kanban_* tools, or gateway/dispatcher board-repair code). In-process delegate children keep their text; leaked markers keep the leak diagnosis.

## How it works

The refusal text is built in `kanban_db_fence.py`, extracted from `kanban_db.py`.

## Code

`hermes_cli/kanban_db_fence.py`, `hermes_cli/kanban_db.py`.

## Tests

`tests/tools/test_worker_terminal_fence_lineage.py` pins that a parent terminal env is identical to a worker's and checks each message.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
