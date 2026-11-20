# Plugin kanban_start_gate hook for token-throttle holds

Patches: **4615–4619** (5011 snapshot).

## Behavior

Right before a claim the dispatcher asks the `kanban_start_gate` hook. `{"hold": true, "reason": "<word>"}` keeps the card ready and unclaimed for that tick (`start_gate:<word>`). No subscriber, a failing callback, a discovery fault, or any other answer starts the card. Nothing is stored: the hook is asked again every tick.

A gated start records `dispatch_skipped` (reason `start_gate`), counts as an expected hold, is named in `held_in_ready` with the plugin's word, and is skipped at pickup. It is not a respawn-guard hold: no cap, no auto-block, no card failure count. Long gate holds do not file pickup-stall repairs.

## How it works

`kanban_start_gate.pre_claim_hold` is the dispatcher's last wait before claim (after the quota governor). Plugin-fault classes are caught by name. Discovery faults fail open with a once-per-process warning. Dry-run reports the gate without claiming.

## Code

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_start_gate.py`
- `hermes_cli/plugins.py`
- `hermes_cli/kanban_diagnostics.py`
- `hermes_cli/kanban_dispatch_expected_holds.py`
- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/kanban_pickup_stall.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_start_gate.py`
- `tests/hermes_cli/test_kanban_llm_pacing.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

A fully gated queue is an expected hold, not a stuck dispatcher. Pickup skips a foreign card this machine's gate would hold. The token throttle remains a plugin; core only exposes the hook.

## plugin?

The hold itself is implemented by a plugin (for example a token throttle). Core adds the `kanban_start_gate` hook and dispatcher wiring; it cannot be replaced by enabling a plugin alone on an older core.
