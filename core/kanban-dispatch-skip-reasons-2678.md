# Dispatcher skip reasons on ready cards

Patches: **2626, 2660**.

## Behavior

- When a dispatcher tick passes over a ready (or review) card without claiming it, the card gets a `dispatch_skipped {reason, detail, until?}` event. Reasons: `skipped_ineligible`, `capsule_refused`, `no_progress_held`, `per_profile_capped`, `claim_deferred` (Board or claim refusals), `owner_down` and `pinned_quota_wall`. Dry-run ticks, and ticks that were locked out or owned elsewhere, record nothing.
- An episode (one ready stretch) holds at most one event per distinct (reason, detail); `pinned_quota_wall` also keys on `until`, so a moved reset appends. At most 16 per episode. A reason that flaps tick to tick (for example `claim_deferred` alternating between `capacity` and a CPU limit) does not add rows after both have been seen.
- A **fresh** card (no earlier run) on the ready lane whose explicit model pin is quota-walled in the live provider pool is held before claim with reason `pinned_quota_wall` and `until` = the reset, instead of spawning a worker that exits 75 into the wall. It is held only when the in-class reroute would not move it (mandatory pin, wall shorter than `kanban.pin_quota_reroute_min_hours`, no eligible same-class peer); otherwise the claim goes ahead and routes to the peer (see [kanban-quota-wall-pin-reroute-2678.md](kanban-quota-wall-pin-reroute-2678.md)). The review lane is never held this way. If the routing policy cannot be read, the card is held.
- Ready-wait notices (chat-wait, parent-unclaimed, queue position in card reads) name the current skip, for example `held: pinned model <model> (<provider>) quota-walled until 10:00Z`, and drop the "bump priority" advice unless the skip is about ordering (`claim_deferred` with detail `capacity`; `per_profile_capped` also counts as ordering). A local card ready 30 minutes or more with free slots and no recorded reason reads `dispatcher skip reason unrecorded` and is told to check the live verdict with `hermes kanban dispatch --dry-run --json`, not to change priority.
- Board diagnostics read a current `pinned_quota_wall` skip with a future `until` as a deliberate `held_in_ready` warning (with `hold_until`), not `stranded_in_ready`.
- The new rows are invisible to readers that look at a card's latest event: they neither start nor end a guard hold, and do not retrigger the nonspawnable, unplaceable, capsule-refusal or `changes_requested` writers.

## How it works

- 2626 adds `hermes_cli/kanban_dispatch_skip.py`. `record_tick` runs from the `observe_dispatch` post-result seam (`kanban_capacity_evidence`), builds the skip list from the `DispatchResult` fields (new `pinned_quota_held` alongside the existing ones) and calls `record`, which appends the event inside a write transaction only while the card is still ready/review. Failures are logged and never stop a tick. `hold_pinned_quota` is called from `lane_admission_refused` (now given the `lane`) and also sets `clock_held` so the stuck warning names the deadline. Wording lives in `words`, `is_ordering`, `advice`, `unrecorded`, `notice_fields` and `notice_advice`; `kanban_queue_position` (`local` lane `skip`, `local_words`), `kanban_parent_unclaimed` and `kanban_chat_wait` consume them. `kanban_capacity_evidence` treats `pinned_quota_wall` as a provider-limit reason.
- Latest-event readers (`note_nonspawnable`, `note_unplaceable`, `_record_refusal`, `goal_run_status`, the guard-hold scan, `_ready_hold_reason`) filter out `dispatch_skipped`.
- 2660 changes dedupe to every skip of the episode, not the newest row, and tracks the current reason in a machine-local, unsynced table `kanban_dispatch_skip_seen` (one row per card: episode anchor `kind@created_at`, current payload, keys recorded, `since`, `seen_at`). `latest()` returns that row, so a notice names the current reason after a flap or past the cap, unless a skip this host never saw (another dispatcher's, synced in) is newer than its last confirmation. Rows refresh at most once a minute while unchanged (a read-only precheck skips the write transaction) and are pruned after 7 days or 5000 rows. Keys this host already recorded in the episode are not appended again, which covers a pickup copy that is evicted and re-imported before the replica has pulled the host's own skip back; the anchor uses event kind and time because re-imports renumber event ids.

## Code

`hermes_cli/kanban_dispatch_skip.py` (new), `hermes_cli/kanban_capacity_evidence.py`, `hermes_cli/kanban_capsule_admission.py`, `hermes_cli/kanban_chat_wait.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_admission.py`, `hermes_cli/kanban_db_dispatch_guard_notice.py`, `hermes_cli/kanban_db_dispatch_preclaim.py`, `hermes_cli/kanban_diagnostics.py`, `hermes_cli/kanban_parent_unclaimed.py`, `hermes_cli/kanban_queue_position.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_dispatch_skip.py`, `tests/hermes_cli/test_kanban_provider_walls.py` (updated: a fresh walled exact pin with no class peer is held, not spawned). Entry path: the dispatcher tick and its post-result recording, the `kanban_dispatch_skip` module API, and the queue-position, chat-wait and parent-unclaimed notice builders. Covered: one event per reason, a pinned-wall hold only for a fresh pin on a live wall, dry-run writes nothing, the review lane never held, unrecorded-skip wording and advice, ordering skips keeping capacity advice, other latest-event readers unaffected, the cap, a flapping detail over 40 ticks (two rows, `latest` tracking), `latest` past the cap, `since` kept and writes throttled, a re-imported copy not re-appending its skip, a new episode starting fresh, another writer's newer skip winning, concurrent recording of one key writing one row, pruning, and a moved reset appending. The `hermes kanban dispatch --dry-run --json` command named in the advice text, and a real synced Board with two dispatchers, are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The reason is advisory: the dispatcher's live verdict decides admission. A skip row can lag by one tick.
- `kanban_dispatch_skip_seen` is created on demand, is local to the host and is not synced; a different host may name a different current reason for the same card.
- The unrecorded-reason wording applies only to the local lane with free slots; a full host keeps the capacity advice.
- 2626 alone holds every fresh walled pin; 2627 (see the quota-wall doc) narrows that to pins the reroute cannot move.

## Plugin

Core change; no plugin required.
