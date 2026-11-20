# Exact-probe passive intake and observed-custody timer bounds

Patches: **4608–4609** (5011 snapshot).

## Behavior

Healthy indefinite listeners can opt out of finite-wait age and timer backstops only for explicitly declared exact task/probe pairs with an external producer and no blocker. Age, counts, errors, lost-probe recovery, and repeated-fire escalation remain. Policy removal restores ordinary guards.

Timer exemption is bounded by observed probe custody, not registration alone. Never-observed local executors and split-custody rows that mask remote silence keep overdue timer recovery. Remote heartbeats can extend a silence deadline; remote silence still wakes once.

## How it works

Config snapshot fails closed on malformed mappings. Quiet-until memos recheck at observation expiry. Metadata blockers disable the repark exemption. Old generation cannot mutate an episode.

## Code

- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_passive_intake.py`
- `hermes_cli/kanban_timed_park.py`
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_wake_episode.py`
- `website/docs/user-guide/configuration.md`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_passive_intake.py`
- `tests/hermes_cli/test_kanban_passive_intake_custody.py`
- `tests/hermes_cli/test_kanban_passive_intake_predicates.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

No persisted exemption schema: exact-probe policy is config. A registration is not liveness. Success and probe errors remain active. Live opt-in is not claimed deployed by this documentation pass.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
