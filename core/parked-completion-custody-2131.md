# Parked native completions remain in exit custody

Mirror ordinals: **2112–2114**.

## Behavior

An eligible native background completion parked in either the process registry or notification park remains visible to orderly-exit receipt collection. Its real completion result is preserved instead of disappearing during a queue-to-park or park-to-routing handoff.

This does not override Stop, notification-off behavior, foreign-session ownership, consumption or delivery acknowledgments. A stale parked duplicate must not resurrect a suppressed or delivered completion. Clearing or expiring a park must not release someone else's independent in-flight hold.

## How it works

Exit collection snapshots registry parking, notification parking, pending queue/in-flight events and session-held events while holding the registry, notification-park and session-held locks together. Separate snapshots could miss an event moving between custodians. Existing unpublished completions and delivery/suppression filtering still participate; collection is not permission to replay every historical event.

Both park claim paths acquire an awaiting/in-flight hold before removing the source copy. Registry ownership predicates run outside its lock, then the implementation rechecks that the selected event identities are still present. Reentrant or competing claims therefore cannot take a removed event from an old snapshot.

Callers release the new hold in `finally`, including routing exceptions. Notification-park delegation held-copy marks are separate: callers also `unhold` those marks after routing. A re-park can acquire its own mark without consuming an unrelated hold.

## Code

- `tools/process_registry_readopt.py`: `take_parked_events` presence recheck and hold-before-removal contract.
- `tui_gateway/notification_park.py`: `claim` gains an in-flight hold and documents both release obligations.
- `tui_gateway/exit_job_receipts.py`: coordinated pending-completion snapshot.
- `tui_gateway/session_notifications.py`: finally-based releases for registry and notification park routing.
- 2112 changes production custody; 2113 broadens suppression/release controls; 2114 table-drives destination setup in receipt fixtures without changing production behavior.

## Tests

Source tests were inspected, **not run here**.

`tests/tui_gateway/test_shutdown_job_receipts.py` extends `test_finished_notice_survives_orderly_restart` to refused, held, queue, registry-park and notification-park destinations. It covers claimed parked custody at shutdown, real exit-code preservation, registry reentrant presence rechecks, error cleanup, and queue/park suppression for stop, quiet, foreign session, consumed and delivered controls. Disposal controls verify clear/expiry preserve independent holds.

`test_notification_poller_idle.py` checks production poller claim routing holds during dispatch and releases on both success and exception. `test_notification_wait.py`, `test_readopted_completion_parking.py` and `tests/tools/test_process_orphan_readopt_tui.py` update callers to release their acquired holds and retain wake/owner assertions.

These tests establish the targeted handoff/receipt contracts; they are not a live restart proof or an end-to-end exactly-once delivery guarantee.

## Applying

Use [README.md](README.md)'s complete snapshot replay and integration recipe. Install the complete custody/caller changes together: updating only the claim API leaves old callers leaking holds. Run the listed Python test modules in a disposable source checkout with the supported dependencies before deployment. No gateway was restarted for this doc.

## Gotchas

- Always release claimed in-flight holds, even if routing raises; notification-park delegations also require their distinct `unhold`.
- Predicates must not run under the custody locks. The post-predicate presence recheck is necessary, not redundant.
- This covers orderly exit custody. It does not establish historical causes of earlier missed notices, arbitrary-crash durability, or recovery of all old chats.
- Receipt visibility and acknowledged delivery are different states; suppression remains authoritative.

## Could this be a plugin?

No for the custody fix itself. The source queues, parking stores, routing cleanup and exit fence are core synchronization boundaries. A plugin can display receipts but cannot safely replace their atomic handoff by sampling events later.
