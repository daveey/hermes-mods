# Desktop: update-held prompts explain themselves and survive an idle Stop

Patch **1998**: source `e634c118c1d96799f0800de661bc131f1a1a59f0`, "fix(desktop): explain update-held prompts and
preserve them on idle Stop [daveey]". It extends the core-update admission hold queue described in
[update-hold-queue-survives-restart.md](update-hold-queue-survives-restart.md).

## Behavior

- **Reason on the reply.** An idle `prompt.submit` that is refused *only* by the core-update admission hold is
  still queued, as before, and its reply now says why: `queued_reason: "core_update"` and
  `queued_started_at`. `queued_started_at` is the hold's start in Unix seconds, taken from the mtime of the
  `holds/hermes-sync` marker under the installation root's `core-update` directory. It's `null` when that
  time can't be read. A second send merged into the same queued turn keeps the reason and the hold time.
  A queue caused by a briefly busy or unreadable fence carries no reason.
  The time comes from the admission hold, never from the Kanban `drain-hold` record.
- **Reason on the snapshot.** The live-session `queued` snapshot carries the same two fields, so a client that
  reconnects re-renders the waiting state.
- **Desktop label.** The queue panel row and the user bubble both show "Waiting for a Hermes update (started
  HH:MM), sends automatically", or the same text without the time when it's unknown. The label shows
  without expanding the row and offers no "Send now". The time is local `HH:MM`. All nine
  Desktop locales get the string (`composer.queueUpdateNote`). The label clears when the queued turn
  starts. The owner merges every held send into one turn, and that turn's `message.start` marks every
  held bubble as started. Ordinary queued bubbles never get this text.
- **Idle Stop keeps the prompt.** A plain Stop gesture on a session that is idle and holds an update-held
  queue leaves the queue untouched. The plain gestures are: no origin (e.g. TUI Esc), `stop_button`, `esc`,
  `tile`, `tile:esc`, `slash` and `transcript`. Such a Stop sets no cancel flag, doesn't bump the queue
  generation and sends no child interrupt frame. The prompt still runs when the hold lifts, including on
  the next backend after the update restart. The session-key replay marker stays. Only an earlier
  turn's stale marker key is cleared.
  - The Stop still cancels a post-turn background review, if one is running.
  - On the Desktop, an idle Stop doesn't set `interrupted`, so the held turn's later stream is accepted.
    A Stop pressed while the held submit is still in flight is undone when the queued ACK arrives.
- **Unchanged cases.** These still clear the queue:
  - stopping a running turn (including a compute-host child turn that is in flight while the parent's
    `running` lags);
  - an idle Stop on an ordinary queue;
  - the non-plain origins `delete`, `rewind`, `send_now` and `plugin:*`;
  - non-user teardown (`backend_exit`, `lease_takeover`, `session_close`, orphan reap).
  An ordinary Stop of a running turn still latches `interrupted` on the Desktop and drops late deltas.
- **Delete.** Delete on a server-queued card cancels its admission at the authority, held or not.

## How it works

- Backend:
  - `hermes_cli/admission_fence.hold_started_at(root)` `lstat`s the marker. A dangling symlink still
    counts as a hold. It returns `None` on error or a non-positive time.
  - `hermes_cli/backend_retirement.update_hold_started_at(home)` resolves the root and never raises.
  - `tui_gateway/session_auto_continue._queue_submit_behind_update_hold` reads `last_refusal() == "held"`
    before any other reservation can overwrite it, stamps the envelope, and returns the two reply fields.
    `_queued_prompt_snapshot` copies them, and `_update_held_queue` checks whether any envelope is
    update-held.
- Stop: `methods_session_interrupt` computes `keep_held` from `_HELD_QUEUE_KEEPING_ORIGINS` and passes it
  to `session_lifecycle._interrupt_session_turn(..., keep_update_held_queue, outcome)`. That function
  decides the keep once, under `history_lock` and before any compute-host I/O. A Stop counts as idle when
  `running` is unset, no run thread is alive, and no `_compute_host_turn_id` is set. The decision is
  returned through `outcome`, so callers act on it instead of re-reading state.
  `_recover_unreachable_compute_host_turn` skips its queue clear when the queue was kept.
- Contracts: `QueuedReason` enum, plus `PromptSubmitResult` and `InflightTurn` fields in
  `tui_gateway/contracts`. These are regenerated into `apps/shared` (OpenRPC JSON and generated TS).
- Desktop:
  - `submit.ts` carries the fields into `trackPendingSubmission`. A held receipt is keyed by admission
    id, with `inputId` set to the submission id. It drops the optimistic duplicate and resets
    `interrupted: false`. The legacy path (a backend that predates `submission_id`) labels the
    optimistic bubble instead.
  - `pending-submissions.ts` (`queueHold`) projects and clears the fields. It also paints the held card
    straight from the ACK.
  - `queued-projection.ts` builds both reconnect projections with the hold fields.
  - `message-equivalence.ts` compares the two fields.
  - `chat-messages/tool-parts.ts` adds `holdsUpdateHeldPromptIdle` and handles the merged start.
  - `rewind.ts` adds `stopLatchesInterrupt`, which both Stop paths (primary and tile) use.
  - `update-held-note.tsx` renders the label.

## Code

Backend: `hermes_cli/admission_fence.py`, `hermes_cli/backend_retirement.py`,
`tui_gateway/session_auto_continue.py`, `tui_gateway/methods_session_interrupt.py`,
`tui_gateway/session_lifecycle.py`, `tui_gateway/contracts/{common,prompt_voice,sessions}.py`.

Desktop: `apps/desktop/src/app/session/hooks/use-prompt-actions/{index,rewind,submit}.ts`,
`apps/desktop/src/app/chat/session-tile-actions.ts`, `apps/desktop/src/app/chat/composer/queue-panel.tsx`,
`apps/desktop/src/app/session/hooks/use-session-actions/{message-equivalence,persisted-live-turn,queued-projection,utils}.ts`,
`apps/desktop/src/components/assistant-ui/thread/user-message.tsx`,
`apps/desktop/src/components/chat/update-held-note.tsx`, `apps/desktop/src/lib/chat-messages/{index,tool-parts,types}.ts`,
`apps/desktop/src/lib/chat-runtime.ts`, `apps/desktop/src/store/{composer-queue,pending-submissions}.ts`,
`apps/desktop/src/types/hermes.ts`, i18n (`ar de en es fr ja ru zh zh-hant`, `types.ts`), `locales/_keys.desktop.json`.

Shared: `apps/shared/src/gateway-contract.{generated.ts,openrpc.json}`.

## Tests

I didn't run these tests for this document. They're listed from the source diff.

- `tests/tui_gateway/test_update_hold_submit_queue.py` (backend, gateway-level):
  - Reply and snapshot fields:
    - `test_reply_names_the_update_hold_and_when_the_hold_was_set`
    - `test_hold_time_comes_from_the_admission_hold_not_the_kanban_drain_hold`
    - `test_unreadable_hold_time_is_null_not_a_guess`
    - `test_a_merged_second_send_keeps_the_reason_and_hold_time`
    - `test_busy_fence_refusal_queues_without_the_update_label`
    - `test_live_snapshot_carries_the_reason_for_a_reconnecting_client`
    - `test_hold_started_at_reads_the_marker`
  - Stop handling:
    - `test_idle_stop_keeps_the_update_held_prompt_and_it_still_runs[None|esc|stop_button|slash|tile]`
    - `test_idle_stop_keeps_it_for_the_next_backend_after_an_update_restart`
    - `test_non_stop_gestures_clear_the_held_queue_as_before[delete|rewind|send_now|plugin:hermes-bots]`
    - `test_idle_stop_still_clears_an_ordinary_queue`
    - `test_stop_of_a_running_turn_still_clears_the_queue`
    - `test_non_user_teardown_clears_the_held_queue_even_if_asked_to_keep[backend_exit|lease_takeover|session_close]`
    - `test_orphan_reap_with_keep_still_clears`
    - `test_marker_retirement_uses_the_stops_own_decision_not_a_later_reread`
    - `test_interrupt_helper_keeps_its_bool_contract_and_reports_the_keep_outcome`
  - Delete under the hold: `test_delete_under_the_hold_discards_the_prompt_for_the_next_backend_too`.
  - Compute host:
    - `test_idle_stop_on_an_unreachable_compute_host_keeps_the_held_prompt`
    - `test_idle_held_stop_after_an_earlier_isolated_turn_sends_no_child_interrupt`
    - `test_stop_while_a_child_turn_is_in_flight_interrupts_it_even_if_parent_running_lags`
    - `test_idle_held_stop_then_hold_lift_runs_the_turn_on_a_real_compute_host`
- Desktop (Vitest):
  - `use-prompt-actions/update-held.test.tsx`, each case run on both the primary and tile surfaces, with
    current and legacy backends:
    - "submit preserves update metadata … and idle Stop retains it"
    - "Stop while the held submit is in flight does not drop the held turn"
    - "Stop after the held reply is a no-op for the later held turn". This case also checks that a
      running-turn Stop still drops a late straggler.
    - "legacy merged held sends all start with the merged turn"
  - `composer/queue-panel.test.tsx`: "shows update-held reason without expanding (known start: true|false)".
  - `composer/queue-slot.test.tsx`: "Delete cancels a server-queued card at the authority (update-held:
    false|true)".
  - `thread/update-held-user-message.test.tsx`, using the real `Thread` renderer:
    - "renders the actual reconnect queued bubble with update timing … clearing on start"
    - "renders a held submit bubble whose id is optimistic, and clears its label on start"
    - "keeps ordinary queued bubbles free of update copy"
  - `use-session-actions/queued-projection.test.ts`: metadata changes are published.
  - `use-session-actions/live-turn-remainder.test.ts`: the queued row keeps the hold fields.
  - `store/pending-submissions.test.ts`: "projects update metadata through canonical reconnect and live
    snapshots, clearing it when the hold ends".

**Untested:**

- The `transcript` and `tile:esc` origins. They're in the keep set but not in the parametrized cases.
- Non-English locale strings. No test reads them.
- The TUI and CLI clients. No TUI render test exists; only the backend reply is tested.
- A real packaged Desktop against a real update cycle.

## Applying

Apply with the series (README § Applying). The patch touches the shared i18n files and the generated
contract files, so it depends on the earlier patches' versions of those files. If it conflicts, regenerate the
OpenRPC/TS contract from `tui_gateway/contracts` rather than hand-merging JSON.

## Gotchas

- **The hold time is the marker's mtime.** `set_hold` never rewrites the marker, but anything else that
  `touch`es it moves the displayed start.
- **The label shows local time.** It's `HH:MM` with no date, so a hold that crosses midnight shows only
  the time.
- **New Stop origins.** A Stop origin added later is outside `_HELD_QUEUE_KEEPING_ORIGINS` and clears
  the held queue until someone adds it to that set.
- **Legacy backends.** A backend without `submission_id` support gets one retried `prompt.submit` (the
  legacy fallback), which the tests count as 2 calls.

## Plugin boundary

There's no plugin equivalent. The decision is made inside the gateway's interrupt path under the session lock,
and in the Desktop's core prompt/stream state machine. No plugin hook sits at either point.
