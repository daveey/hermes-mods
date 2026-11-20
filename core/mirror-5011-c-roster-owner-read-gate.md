# Roster owner reads gated on input and store generation

Patches: **4584** (5011 snapshot).

## Behavior

Desktop roster polls need membership, not complete session payloads or token flushes. Stable owners skip resolution. Route namespace discovery runs only when a live process needs the legacy fallback. Activity clocks stay fresh independently of membership memo invalidation.

## How it works

Memo is per SessionDB object/connection, keyed on actual inputs and committed generation (`total_changes`, `data_version`, namespace, new owner candidates). Pending transactions are uncached. Narrow `get_session_list_row` avoids the unrelated payload path. Replacement DBs with the same version never inherit owners.

## Code

- `tui_gateway/methods_subagents.py`
- `tui_gateway/roster_owner_reads.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/tui_gateway/test_roster_cost_replay.py`
- `tests/tui_gateway/test_roster_input_gate.py`
- `tests/tui_gateway/test_roster_owner_invalidation.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Process exit and new owners are not memoized. A change during resolve is not published. Synthetic replay timings are not live whole-RPC acceptance.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
