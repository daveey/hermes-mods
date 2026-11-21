# Kanban: owner-wake reopens a contained authority

Mirror snapshot 5406; ordinals 5386, 5387.

Entries:

- **5386**: kanban: let owner-wake reopen a contained authority
- **5387**: kanban: route authority recovery through authenticated owner-wake tool

## Behavior

- **5386**: first helper-level attempt, unreachable from a fenced worker shell.
- **5387**: route through `kanban_unblock(task_id, board, reopen_authority=…)`, using worker authority on its own board and containment evidence on the target board; run and policy are revalidated inside the write transaction. Shell fences are unchanged.

## How it works

- `kanban.py`, `kanban_requeue.py`, `kanban_db_operator.py`, `tools/kanban_tools_authority_reopen.py`, schemas and self-heal tooling.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_requeue.py`
- `hermes_cli/kanban_db_operator.py`
- `tools/kanban_tools_authority_reopen.py`
- `tools/kanban_tools_schemas.py`
- `tools/kanban_tools_self_heal.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/tools/test_kanban_owner_wake_authority_reopen.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Only the nominated done authority can be reopened.

## Why core instead of a plugin

Authorization sits at the tool boundary in core.
