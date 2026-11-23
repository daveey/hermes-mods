# Once-per-episode escalation for stranded parked cards

Source patch ordinals: 5808–5812.

## Behavior

The dispatcher surfaces sufficiently old, genuinely unowned `triage`/`blocked` cards once per park episode. Live chats, open decision/recovery cards, lane gates and declared waits retain their ownership/exclusions instead of being converted indiscriminately into human work.

## How it works

`kanban_stranded_park` tracks the park episode rather than resetting age on comments. Defaults are `kanban.stranded_park_seconds=7200`, `stranded_sweep_interval_seconds=300`, `stranded_owner_idle_seconds=86400` and `stranded_stale_defer_seconds=21600`. `triage_owner_chat` optionally names the desk chat.

A bounded stale-sweep deferral prevents endless postponement for busy cards. Finished recovery cards no longer own the park. Normal escalation records a human request with unblock/archive choices and binds the desk; a contained card receives only the desk link because containment refuses the request write. Spend-stop unblock routes through promotion rather than bypassing its decision policy.

## Code

- `hermes_cli/kanban_stranded_park.py`
- `hermes_cli/kanban_triage_sweep.py`
- `hermes_cli/kanban_block_policy.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/config_defaults.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_stranded_park.py` covers young parks, comment age, live/archived/hidden owners, local/remote desk routing, exclusions, dependencies, containment, per-episode deduplication, throttle/configuration, recovery ownership, stale deferral, ask lint and answer routing. `test_kanban_spend_decision_routing.py` covers owning-chat route behavior.

## Applying

Apply the policy and review follow-ups in order. Configure the desk as `<gateway>/<profile>/<session>` only in private operational configuration; keep public examples generic.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

This is a backstop, not permission to release a card or to turn all agent waits into human asks. Explained retained waits receive a later classifier refinement. The desk link alone cannot defeat containment.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
