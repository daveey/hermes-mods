# Kanban LLM pacing: one-run recovery floor, pacing in ready hints, expected-hold classification

Mirror snapshot 5011; ordinals 4357, 4358, 4394, 4367, 4423.

Entries:

- **4357**: fix(kanban): give paced headroom a one-run recovery floor
- **4358**: test(kanban): occupy the paced recovery floor in empty-window hold tests
- **4394**: test(kanban-llm-pacing): a no-reset reading must subtract its usage from the sustainable rate
- **4367**: fix(kanban): show pacing admission in ready hints
- **4423**: fix(kanban): distinguish admission holds from dispatcher stalls

## Behavior

- **Recovery floor (4357, 4358)**: a PACED provider at idle was an absorbing zero (`floor(avg_running * factor)` admitted nothing, so the next average stayed zero). After deferrable scaling one shared provider slot is now preserved; HELD still admits none. Known quota walls still reroute before pacing, so the destination is governed instead of treating ordinary pacing as a wall. Empty-window hold tests now occupy the floor explicitly. The kanban user guide is updated.
- **Ready hints (4367)**: `ready_hint` reuses the stall-notice pacing decision and observation renderer, so a card waiting on pacing shows why. Busy-backlog precedence and the fail-open wording are unchanged.
- **Missing-reset pin (4394)**: tests prove a usage reading with no reset time still subtracts its usage from the sustainable rate (no-reset 40%-used window is not treated like a fresh one), including the PACED-vs-OPEN flip and its admit-limit consequence.
- **Expected holds (4423)**: the dispatcher's stall detector now treats typed pacing waits, clock holds and host-pressure deferrals as expected waits instead of "ready but nothing spawned" stalls. A new diagnostic-only module classifies per-card holds from the typed dispatch result using a positive allowlist (an unknown outcome never masks a failure) and a monitor reports a wait with no reset after 15 minutes, or a timed wait 15 minutes after its first reset.

## How it works

- `kanban_llm_pacing.py` computes the admit limit; the floor is applied after scaling. `kanban_parent_unclaimed.py` renders the ready hint.
- `kanban_dispatch_expected_holds.py` validates typed pacing rows (provider state, positive int limit, running >= limit, finite reset) and builds `(board, task)` hold details; a `HoldMonitor` is held per dispatcher tenure, observes each tick and prunes waits for observed boards. `kanban_db_dispatch_clock.py`, `kanban_ops.py` and the gateway watcher wire the bad-tick counter to it.

## Code

Shipped non-test paths (union over these entries):

- `gateway/kanban_watchers.py`
- `hermes_cli/kanban_db_dispatch_clock.py`
- `hermes_cli/kanban_dispatch_expected_holds.py`
- `hermes_cli/kanban_llm_pacing.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_dispatch_expected_holds.py`
- `tests/hermes_cli/test_kanban_llm_pacing.py`
- `tests/hermes_cli/test_kanban_llm_pacing_empty_windows.py`
- `tests/hermes_cli/test_kanban_llm_pacing_followups.py`
- `tests/hermes_cli/test_kanban_llm_pacing_missing_reset.py`
- `tests/hermes_cli/test_kanban_notice_pacing_admission.py`
- `tests/hermes_cli/test_kanban_pacing_recovery.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- The monitor is observational: it never writes admission state.
- Unknown or incomplete dispatch-result shapes count as unexpected and keep the stall counter running.
- Legacy fixtures with an empty pacing window had to be updated to occupy the floor.
