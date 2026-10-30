# Kanban park conditions and capability-owner archive scope

Patches: 1258 1259 1264 1274

- `patches/1258-fix-kanban-a-park-never-waits-on-something-already-t.patch`
- `patches/1259-fix-kanban-a-worker-may-archive-the-capability-owner.patch`
- `patches/1264-fix-kanban-wake-a-park-when-its-producer-card-finish.patch`
- `patches/1274-fix-kanban-forward-a-worker-node-s-archive-of-a-rela.patch`

## Behavior

- 1258: done cards are excluded from inferred future producers. Explicit local or remote done producers are refused with `producer_done`. A wake probe already passing at park time is refused with `wake_check_already_true` through schedule, waiting continuation, timed block and CLI schedule. A not-yet probe or timeout remains parkable. Shared-blocker refusals point to `clear_blocker=true` rather than silently changing the blocker.
- 1259: a worker may archive a done or unclaimed capability-owner card serving its own task or a card that task created. Claimed owner cards, unrelated owners and plain-key lookalikes remain refused.
- 1264: when an ordinary wake probe says not yet, a declared producer's completion can still wake the park. Local completion and remote completion are checked; running, review, blocked and unknown/offline remote answers do not wake it. A probe already using `wait-check task` on that producer is not second-guessed.
- 1274: the archive tool can forward a missing local owner card's archive home when the worker's capability route identifies that remote owner. The home applies the same scope rule, archives an allowed owner and records the forwarding task/run. It returns its own refusal for a claimed owner and rejects forged requests for another card's owner.

## How it works

1258 adds `_completed_at` and `producer_done_error` to awaiting validation. Remote confirmation happens before the write transaction. Park-time dry runs carry the wake-probe marker so implicit status baselines agree with dispatcher probes. A placement producer finishing between settlement and confirmation triggers one guarded re-settlement rather than a park.

1264 rechecks the declared producer after a not-yet probe, using local status or `check_remote`. Remote checks are throttled to once per 15 minutes per gateway/reference. Completion records a `wake_probe_stale` event with reason `producer_finished`, a dispatcher comment and wake context. The schema explains that local `show`/`list` is not a remote-board query and that already-met statuses need `--from` when waiting for a change.

1259 derives ownership from `capability-owner:<waiting>:...` and checks the waiting task or its creator. 1274 extracts `archive_scope_for` so the home can check a request's explicit worker scope rather than its environment. The worker appends `capability_owner_archive_requested` on the waiting card; Board events carry it home. Pulling the event wakes the dispatcher, whose reclaim phase applies requests and appends a matching result. The worker waits up to 60 seconds for that result. The code returns `archive_pending` if the answer has not arrived; that timeout path is untested by the new cases.

## Code

`hermes_cli/kanban_awaiting.py`, `kanban_cli_park.py`, `kanban_placement_forward.py`, `kanban_wake_check.py`, `kanban.py`, `kanban_db.py`, `kanban_db_dispatch.py`, `kanban_db_sync.py`, new `kanban_owner_archive_relay.py`, plus `tools/kanban_tools.py` and `tools/kanban_tools_schemas.py`.

## Tests

Included tests were inspected, not re-run for this documentation update:

- 1258: `tests/hermes_cli/test_kanban_park_done_producer.py` covers inferred done producers (including a relayed worker), explicit local/remote done refusal, passing/not-yet/timeout probes, waiting continuation, timed block guidance and CLI schedule refusal. `tests/hermes_cli/test_kanban_placement.py::test_forwarder_completes_when_the_hub_card_finishes_between_settle_and_park` covers the settlement race. Existing CLI/wake-check fixtures are adjusted for the stricter dry run.
- 1259: `tests/tools/test_kanban_capability_owner_archive.py` exercises the archive tool on an owner of a created probe, an unclaimed owner of the worker itself, another task's owner, a claimed owner and a plain-key lookalike.
- 1264: `tests/hermes_cli/test_kanban_park_producer_finished.py` tests remote-done wake, nonterminal and offline controls, remote throttle, local completion, schema guidance and leaving a same-card wait-check alone. It checks ready status, one stale-probe event, comment and rendered context. Remote calls use a stand-in; actual cross-gateway SSH is untested here. The archived-producer branch has no dedicated new case.
- 1274: `tests/tools/test_kanban_owner_archive_relay.py` exercises the tool against worker/home board fixtures: a done relayed owner, an owner serving the worker's created probe, home refusal of a claimed owner, no forwarding for an unrelated card and home rejection of a forged owner request. It checks home archive status and forwarded run metadata. Transport is a fake Board round trip, not a live Board deployment. CLI forwarding, pending timeout and failed-outbox rollback are untested by these new cases.

## Applying

Apply the complete numbered series at the documented merge-base with `git am -3 --empty=keep`; see `README.md`. The patches share park, scope and sync machinery and are not independent.

## Gotchas

- A passing probe is not a reason to sleep. Read and act on the producer's result, or describe a different unmet condition.
- `done` is refused at park time; the later producer-completion fallback is for a producer that finishes after the park.
- `archive_pending` is queued work, not confirmed archival. The worker must not claim the owner is archived merely because forwarding succeeded.
- Forwarding is a tool-side missing-card fallback scoped to a recorded home-relay route, not an arbitrary remote archive API.
