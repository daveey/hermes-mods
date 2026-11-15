# Redelivering automated wakes refused by a held quota wall

Mirror snapshot 4352; ordinals 4218, 4246, 4247.

Entries:

- **4218**: fix(gateway): redeliver an automated wake a held quota wall refused, once, after the reset
- **4246**: fix(gateway): wall-replay drops stale plans at boot and keeps its retry budget across restarts
- **4247**: test(gateway): wall-replay fixture authority is keyed by its profile home, so a redelivery's own policy fits _admit's scope

## Behavior

An automated Kanban notifier or background-process wake sent to a chat whose pinned route sits behind a held subscription wall was refused before the agent was built, and nobody resends an automated notice. Now the refusal plans one redelivery after the reset.

- After a pre-build wall refusal (no API calls, `route_refused` / `auth_permanent`, a known reset) of an unattended route wake (`kanban-wake` / `background-wake`, principal `automation:<route>`), one redelivery is planned at `max(reset + 60 s, now + 60 s)` (none past 7 days), stored durably in `state_meta` and armed in process, re-armed at authority boot. It re-admits through `admit_route_wake` under the deterministic identity `wall-replay:<tail>`, so it is an exact replay and is never folded into a waiting Kanban wake. A redelivery refused again plans nothing. Transient refusals or a missing adapter retry (bounded); permanent refusals drop. The refused reply says when the notice will be delivered again; a person's message keeps the held reply unchanged (4218).
- 4246: plans due more than a day ago are dropped at boot (`STALE_S`) or when firing that late; spent tries are persisted in the plan so the retry budget (`RETRIES_MAX`) spans restarts; an unexpected exception from admission counts as a retry instead of escaping.
- 4247 is test-only: the fixture authority is keyed by `str(profile home)` as production authorities are, so a redelivery's turn task scope matches.

## How it works

- `gateway/session_wall_replay.py` (planning, arming, `fire_replay`, `_retry`), `session_automation.py`, `session_authority.py`, `session_ingress.py`, `run_turn_runner.py` (`_resolution_failure_result` stamps `failure_resets_at` from `SubscriptionQuotaUnavailable`).

## Code

Shipped non-test paths (union over these entries):

- `gateway/run_turn_runner.py`
- `gateway/session_authority.py`
- `gateway/session_automation.py`
- `gateway/session_ingress.py`
- `gateway/session_wall_replay.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/gateway/test_wall_replay_automation_wake.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Delivery stays at most once by deterministic identity; failure costs are noise or staleness, not duplicates.
- A plan whose adapter never returns is dropped after the retry budget across restarts.

## Plugin?

None.
