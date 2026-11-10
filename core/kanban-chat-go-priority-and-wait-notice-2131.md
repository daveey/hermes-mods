# Chat go on a human_request card: rank, queue, unclaimed notice

Patch **2098**.

## Behavior

- `kanban_unblock` from a chat/orchestrator path, when the card carried a `human_request`, raises stored priority to at least 10 (never lowers) and logs `priority_raised` (`human_go`). Worker requeue grants are unchanged.
- The unblock result adds `queue` (queue position) and a `queue_hint` ("bump priority, re-place, or do it in this chat; don't promise a pickup") when cards ahead meet or exceed host slots or the card is unplaceable.
- Each dispatcher tick runs `kanban_chat_wait.sweep`: a chat-owned card (notify subscription or session id) ready and unclaimed 30 min to 6 h gets ONE `chat_wait_unclaimed` event per ready episode. Gateway wakes and TUI notifiers deliver it as a wake-worthy notice.

## How it works

`handle_unblock` reads `had_human_request` in the unblock precondition (the unblock clears it), then `raise_for_human_go`; failure is advisory. `kanban_parent_unclaimed.post_notice` now takes a `kind`. The new kind is registered in gateway terminal/wake kinds and TUI notify kinds with formatter functions.

## Code

- `hermes_cli/kanban_chat_wait.py` (new), `hermes_cli/kanban_parent_unclaimed.py`, `tools/kanban_tools_self_heal.py`.
- `gateway/kanban_watchers_notifier.py`, `tui_gateway/session_notifications.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/tools/test_kanban_unblock_queue.py`: chat unblock of a human-request card raises priority; never lowers; keeps priority without a human request; worker unblock keeps priority; queue and hint when full, no hint when a slot is near; subscribed card ready 31 min gets one notice; claimed card none; short wait or no chat none; new ready episode notifies again; notice kind delivered and wakes; dispatch tick runs the sweep. Desktop display is untested.

## Applying

Apply ordinal(s) 2098 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Builds on parent-first dispatch ([kanban-parent-first-dispatch.md](kanban-parent-first-dispatch.md)) and the queue read from [kanban-ready-card-queue-reads-2131.md](kanban-ready-card-queue-reads-2131.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

Backlog older than 6 h at first sight is skipped (the card read's hint covers it), bounding a burst after deploy. The priority bump only applies to cards that had a human request.

## Could this be a plugin?

No. It spans the dispatcher tick, unblock tool and two notifier paths.
