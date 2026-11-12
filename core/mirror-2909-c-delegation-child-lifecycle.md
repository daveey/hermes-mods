# Terminal delegations close their own child session lineages

Patches: **2861, 2890**.

## Behavior

Recovery of a dead-owner delegation and stall force-finalization close still-open child rows belonging to that unit. Startup cleanup also closes old idle subagents whose actual delegator has ended, while preserving live/excluded/pinned work.

## How it works

- **2861:** persist task-index → child-session IDs at dispatch. Recovery uses `delegation_unknown`, stall finalization uses `delegation_stalled`; both refresh affected outcomes. A separate ended-parent sweep uses `parent_ended_reap`, avoiding a duplicate killed-subagent notice.
- **2890:** walk past a child’s own compression segments to find its real delegating parent. Add pinned-lineage exclusions to the startup sweep. Recursive child closure follows verified compression continuations, not delegate grandchildren of a segment, and does not overwrite already-ended segments’ reasons.

## Code

- `hermes_state_maintenance.py`
- `tools/async_delegation.py`
- `tools/delegate_tool_dispatch.py`
- `tui_gateway/session_reaper.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_async_delegation_child_row_end.py` covers persisted IDs, dead-owner/stall closure, foreign parent/source rejection, ended vs open/recent/excluded delegators, startup integration, compressed tips, pinned continuations and untouched grandchildren.

- `tests/tools/test_async_delegation_child_row_end.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

An ended compression segment is not by itself an ended delegator. Worker-process units without forwarded child IDs are not covered by owner recovery; their rows still depend on ended-parent or heartbeat-gated orphan cleanup. Distinct end reasons are bookkeeping, not evidence the child completed successfully.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
