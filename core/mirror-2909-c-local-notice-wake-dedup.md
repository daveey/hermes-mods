# One local Kanban wake carries its TUI sibling’s notices

Patches: **2862, 2863, 2864, 2865, 2866, 2867, 2886, 2887**.

## Behavior

A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. The wake carries the notice lines, while TUI consumers retain delivery responsibility if the wake cannot land.

## How it works

- **2862:** introduce sibling matching by card/chat/thread, covered wake kinds and notice carriage in notifier turns; wire the legacy poller and unloaded sweeper.
- **2863:** successful wake delivery settles the TUI cursor or advances an in-flight claim’s rewind floor, so archive/removal of the local subscription does not resurrect a completion.
- **2864:** silence only events at/below the local subscription’s acknowledged cursor, not merely because a local row exists. While a young covered event is still owed, leave TUI unclaimed; after the 600-second window or a dropped wake row, TUI may deliver.
- **2865:** provisional failures do not block settlement; their finalized failure owns the notice.
- **2866:** carry all still-owed notice lines in a wake batch, including preceding status/quota/re-pin events, and settle only carried otherwise-uncovered IDs. Recheck after a poller claim and rewind if a covered event raced the first check.
- **2867:** preserve carried notice lines when a newer same-card wake replaces its premise; validate `notices` metadata. Give the unloaded sweeper the same post-claim race check.
- **2886:** keep deferring while a core-drain hold prevents the waking leg’s delivery, even for old events.
- **2887:** stamp the last observed hold at fleet-root `core-update/notifier-hold-seen`; all legs use the shared post-hold window. Defer while held or seen-held within 600 seconds, then use the event age again.

## Code

- `gateway/kanban_wake_sibling.py`
- `gateway/kanban_watchers_notifier.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/session_notifications.py`
- `gateway/kanban_tui_owner.py`
- `gateway/kanban_wake_fold.py`
- `gateway/session_notifier_drain.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_kanban_notify_wake_one_turn.py` composes local notifier, legacy TUI poller and unloaded sweeper in both orders; tests archive/in-flight rewind, missing/failing adapter fallback, provisional/final failures, mixed notices, claim races and folded notice preservation. Long-hold regressions cover TUI-first and wake-first after lift, plus eventual fallback if the wake never lands.

- `tests/gateway/test_kanban_notify_wake_one_turn.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

The post-hold stamp is the last time any notifier leg observed the hold, not an exact recorded lift event; future stamps do not extend it. Notify-only local rows cover nothing. Read errors favor possible duplicates over a lost notice. Oversized folded cards may drop the oldest retained notices to respect the existing text cap.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
