# Re-park escalation: natural waits and the probe fire cap

Patches: **2497, 2528, 2538**.

## Behavior

A card that parks on an opaque wake probe, wakes when the probe fires, and parks again on the same probe used to count each park as the same premise. The third park escalated: the card went to `todo` behind an "Unstick" owner card and lost its `wake_check`. Now:

- A `condition_met` wake by the same probe ends the same-premise run, because the condition really held. A re-park after it is treated as a natural window that closed again, not a stuck premise. Parks woken only by their deadline, or after a different probe fired, still count and still escalate.
- To stop a broken probe cycling forever, `PROBE_FIRE_REPARK_LIMIT = 6` fire-then-re-park cycles on one probe escalate the next park. The escalation record carries `fired_reparks`, the owner card is titled "Unstick <card>: probe fired Nx, re-parked each time", and its body says to fix the probe or the worker's check.
- The message returned to the parking worker names the fire count ("its wake probe fired N times and it re-parked each time") instead of "same premise 3 times".
- The count resets on an escalation, a park on another premise, another probe's fire, or other card progress. A human comment does not reset it.
- The escalation owner is never assigned a profile that nothing can run: a candidate is skipped when a new scratch card would enroll in this gateway's worker pool and no active, unheld, pickup-advertising pool member attests `profile:<name>`. An unreadable registry counts as none. With no runnable candidate the owner falls back to triage.

## How it works

In `hermes_cli/kanban_awaiting.py`, `prior_same_premise` now also reads `condition_met` events and stops at one whose `wake_check` hashes to the same `_probe_key`. `probe_fire_reparks` counts trailing fire-then-re-park cycles; `gate` raises `RepeatedPark` with `record["fired_reparks"]` at the limit. Premise-less rows (pacing continues, legacy parks) do not use up `PROBE_FIRE_WINDOW`; only `PROBE_FIRE_SCAN_MAX` bounds the raw event read. `escalate` builds the fire-specific title and lead; `escalated_fired` reads the count from the latest `repark_escalated` event and `escalation_message(task_id, owner, fired)` selects the wording.

The new `hermes_cli/kanban_profile_runnable.runnable(name)` is consulted by `_supervisor` in `kanban_assignment_routing.py`. Related: [kanban-park-invariants.md](kanban-park-invariants.md), [kanban-block-loop-escalation.md](kanban-block-loop-escalation.md), [kanban-wake-check-episodes.md](kanban-wake-check-episodes.md).

## Code

`hermes_cli/kanban_awaiting.py`, `hermes_cli/kanban_profile_runnable.py` (new), `hermes_cli/kanban_assignment_routing.py`, `hermes_cli/kanban_cli_park.py`, `tools/kanban_tools_park_args.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_repark_natural_wait.py`. Entry path: the `kanban_schedule` and `kanban_continue` tool handlers (`tools.kanban_tools._handle_schedule`/`_handle_continue`) with the probe firing driven through `kanban_wake_check.apply_result`. It covers no escalation after repeated fires, deadline-only wakes still escalating, another probe's fire not counting, count reset, the six-fire cap with owner body and `fired_reparks`, reset by escalation, the worker-facing message wording, pacing continues not hiding the cap, and `_supervisor`/`runnable` against a fake worker pool and registry. The CLI park message (`kanban_cli_park._parked_msg`) shares `escalation_message` but is untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- `escalation_message` gained an optional third argument; existing two-argument callers keep the old wording.
- Six fires is a fixed constant, not configuration.
- Cards with a pool configured and no profile-attesting worker now route escalation owners to triage rather than the configured supervisor.

## Plugin

Core change; no plugin required.
