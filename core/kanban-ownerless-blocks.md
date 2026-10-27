# Kanban: refuse ownerless agent blocks

Patch **0451** (`15f2bfb9bd`).

## Behavior

A worker-facing block of kind None, `capability` or `transient` with no `human_request`, `wake_check` or
`clock_only` is refused with error code `agent_owned_block`, naming the three honest options (ask a human, wait on
time or a probe, or `kind=assignment`). Creating a blocked card or a decision-titled card (`for you:`, `choose`,
`decide`, `approve`) without a human ask is refused (`creation_lint_error`). `sweep_agent_owned_blocks` respawns
existing ownerless blocks older than 2 h through triage once, with evidence (`agent_block_escalated` event).

## Code

`hermes_cli/kanban_block_policy.py`, hooks in `kanban.py`, `kanban_db.py`, `kanban_db_dispatch.py`, `kanban_swarm.py`,
`tools/kanban_tools.py`.

## Tests

`tests/tools/test_kanban_tools.py` (tool path), `tests/hermes_cli/test_kanban_cli.py` (CLI path), and adjusted
`test_kanban_blocked_sticky.py`, `test_kanban_graph_human_request.py`, `test_kanban_human_request_entries.py`,
`test_kanban_timed_park.py`. Per the entry-parity rule, new creation paths need a case in
`tests/hermes_cli/test_kanban_create_entry_parity.py`; none was added by this patch (untested).

## Applying

After 0446 and 0447.

## Gotchas

Existing agent-owned blocks are re-spawned once, not deleted.

## Plugin?

No.
