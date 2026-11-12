# Decomposer assigns enrolled cards only to runnable profiles

Patches: **2530**.

## Behavior

- A card enrolled in worker-first placement has its placement fixed at creation: it runs only on a pool worker attesting `profile:<assignee>`. The auto-decomposer used to specify such cards (typically triage cards with no assignee) to a profile no pool worker carries, leaving them queued until someone finished them by hand.
- Now, for the card itself (a single-task assignee, and the root's owner after fan-out), the decomposer keeps only profiles that are runnable for that card. An unrunnable pick falls back to `default_assignee` when that is runnable, else the card's assignee is left unchanged. The roster in the LLM prompt marks other profiles "no pool worker runs it: fanout children only, never this task itself".
- Fan-out children are inserted unenrolled and run on this gateway, so they keep the whole roster. A card that is not enrolled accepts any installed profile, with no registry read. `kanban_specify` assigns nothing and is unchanged.

## How it works

`runnable_for(name, task)` in `hermes_cli/kanban_profile_runnable.py`: a non-enrolled card returns true; an enrolled one requires an active, unheld, pickup-advertising machine in this gateway's pool (every registered machine if no pool is configured) whose labels include `profile:<name>`, regardless of the assignee allowlist. The shared check was factored into `_attested`. In `hermes_cli/kanban_decompose.py`, `_load_routing(root_assignee, task)` fills `card_names`, `card_default` and `card_root` on `_Routing`; `_card_assignee` normalizes the choice; `_format_roster` marks child-only entries; `_apply_fanout` passes `card_root` as the root assignee. Related: [kanban-worker-first-placement.md](kanban-worker-first-placement.md), [kanban-decomposer-board-scope.md](kanban-decomposer-board-scope.md).

## Code

`hermes_cli/kanban_decompose.py`, `hermes_cli/kanban_profile_runnable.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_decompose_runnable.py`. Entry path: `decompose_task` (the decomposer function) with a mocked LLM call, a stub pool registry and real card creation: an enrolled card is kept off an unrunnable profile and the prompt carries the marker; a pick a live pool worker carries is kept; an unrunnable default assignee leaves the assignee unchanged; an unenrolled card accepts any profile; fan-out root versus children. CLI, tool and REST triggers of decomposition are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- An unreadable pool config or registry counts as "no runnable pool" for the pick (it falls back, never invents capacity).
- When the fallback is not runnable either, the assignee stays as it was; the card may remain unassigned.

## Plugin

Core change; no plugin required.
