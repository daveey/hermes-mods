# Kanban: refuse ownerless agent blocks

Patches **0462** (`15f2bfb9bd`), **0596**-**0597** (needs_input).

## Behavior

A worker-facing block of kind None, `capability` or `transient` with no `human_request`, `wake_check` or
`clock_only` is refused with error code `agent_owned_block`, naming the three honest options (ask a human, wait on
time or a probe, or `kind=assignment`). Creating a blocked card or a decision-titled card (`for you:`, `choose`,
`decide`, `approve`) without a human ask is refused (`creation_lint_error`). `sweep_agent_owned_blocks` respawns
existing ownerless blocks older than 2 h through triage once, with evidence (`agent_block_escalated` event).

A `needs_input` block with no `human_request` (and no `wake_check`, `clock_only`, `blocker_id`, `SCHEDULED_UNTIL=`)
is agent-owned too (0596): the sidebar shows an ask-less `needs_input` as active, so David never saw it. The refusal text
points to `human_request` for a step only David can take, or `needs=['host:<gateway>']` / an owner card for a file,
permission or tool another gateway holds. The sweep escalates existing ask-less `needs_input` blocks past 2 h. The
dashboard drag-to-blocked path calls `block_task` directly (a human override) and is covered by the sweep only.

## Code

`hermes_cli/kanban_block_policy.py`, hooks in `kanban.py`, `kanban_db.py`, `kanban_db_dispatch.py`, `kanban_swarm.py`,
`tools/kanban_tools.py`.

## Tests

`tests/tools/test_kanban_tools.py` (tool path), `tests/hermes_cli/test_kanban_cli.py` (CLI path), and adjusted
`test_kanban_blocked_sticky.py`, `test_kanban_graph_human_request.py`, `test_kanban_human_request_entries.py`,
`test_kanban_timed_park.py`. Per the entry-parity rule, new creation paths need a case in
`tests/hermes_cli/test_kanban_create_entry_parity.py`; none was added by this patch (untested).

`tests/tools/test_kanban_block_needs_input.py` (tool path refusal with the verbatim cutover reason, accepted with an
ask + sidebar bucket, sweep escalates ask-less and spares asked). The CLI `block` path shares `block_policy_error` but
has no CLI-level test for needs_input (untested).

## Applying

After 0457 and 0458.

## Gotchas

Existing agent-owned blocks are re-spawned once, not deleted.

## Plugin?

No.
