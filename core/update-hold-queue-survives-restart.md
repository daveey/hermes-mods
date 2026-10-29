# Update hold: a queued message survives the backend restart and is delivered once

Patches: 0900 (fork `2f26519116`), 0901 (`0d1e39ba3e`), 0929 (`4edd4010e0`).

## Behavior

While hermes-sync's core-update drain window holds admission, an idle `prompt.submit` is queued in the backend
(`_queue_submit_behind_update_hold`) instead of being refused with 5035. Before these patches the queue lived only in
that backend's memory, so the update's restart lost it and the chat showed the message as "not sent".

Now:

- The `{"status": "queued"}` reply passes `PromptSubmitResult` (an extra `reason` key used to fail the wire contract).
- The queued text (merged, if several sends were queued) is journaled as an `update_hold_queue` turn marker. After the
  restart, `session.resume` replays it verbatim as the user's own message ("Sending your queued message…"), with no
  auto-continue attempt used and no recovery note. The startup sweep retries it too.
- The marker keeps for 1 h (`UPDATE_HOLD_QUEUE_MAX_AGE_S`), not the 15 min turn window: the drain window holds
  admission up to 15 min and the apply takes 1-4 min more. An ordinary crashed turn keeps the 15 min bound.
- Exactly once:
  - **Queued during the update, then restart:** the next backend replays it once.
  - **Accepted just before the restart:** once the hold lifts and the turn starts, the turn's own marker replaces the
    `update_hold_queue` entry. A turn the SIGTERM killed is continued as an interrupted turn, not sent again. A turn
    that already answered is not replayed.
  - **Renderer reload to the same backend (0929):** Desktop reconnects to the same backend, and `session.resume`
    reuses the live record. While the live queue holds the prompt, the marker is not replayed, and it stays for a
    later restart. During the hold itself the replay was already blocked: `_start_session_work` reserves fresh
    admission, and the hold refuses it. Before 0929 the duplicate was reachable only while admission was open and the
    queue still held the prompt. That is the ≤250 ms before the retry timer drains after the hold lifts, or a queue
    retrying on transient contention. In that window the warm resume replayed the text and the queue drained it too.
  - **Reload that outlasted the orphan grace:** the reaper drops the in-memory queue and keeps the marker, so the next
    resume in the same backend replays it once. The old record's retry timer sends nothing.
  - **Stop under the hold:** `session.interrupt` purges the queue and retires the marker, so the next backend does not
    replay it.

## Five whys (incident 2026-10-28 17:41-17:44 PDT, MacBook)

1. **"go" + "move the repo to metta-ai/ org as well" showed "not sent".** agent.log 17:44:21: `newest user message has
   no reply and no turn is running; shown as not sent`. The merged accept row 653501 (17:41:27) was in state.db with no
   reply.
2. **Why no turn ran:** 17:41:27 `prompt.submit for session 4e94bea8 queued behind the core-update admission hold`.
   The update applied at 17:41:23 under the held admission. At 17:43:49 the backend exited (SIGTERM, `Cancel 1 running
   task(s), timeout graceful shutdown exceeded`) before the hold lifted. The queue was a `session["queued_prompt"]`
   dict plus a 250 ms retry timer, all in that process's memory.
3. **Why the new backend did not take it over:** nothing durable said "pending". The accept row looks the same as an
   unanswered message, and no turn marker was written for a turn that never started. The design note said "persisted
   at accept, so a backend restart cannot lose it", but that covered the row (display), not the delivery.
4. **Why the reply errored too:** `{"status": "queued", "reason": "update_hold"}` failed `PromptSubmitResult` (17:41:27
   `violates its wire contract`). The test asserted the dict, not the contract.
5. **Why it wasn't caught:** the hold-queue tests (t_01a181e61e88e5d4) ran the queue and drain in one process. None
   killed the backend and resumed in a new one, and none reconnected to the same one. That gap is closed by the
   restart, accepted-before-restart, reload and Stop tests below. 0929 came out of the reload test: it failed on 0901.
   The follow-up 17:44:32 `session_busy` on the retry (a busy state.db read as another writer) was fixed by
   `7e8e8f4a59` (turn-lease busy-DB floor). The replay test in 0901 drives that admission.

## How it works

- `tui_gateway/session_auto_continue.py::_queue_submit_behind_update_hold`: after the accept row,
  `record_turn_start(..., reason=UPDATE_HOLD_QUEUE_REASON)` with the merged queue text.
- `_maybe_schedule_auto_continue`: a `held` marker replays `marker["prompt"]` with `display_kind=None`, and uses its
  own age bound. A held marker is skipped while `session["queued_prompt"]` is set (0929).
- `tui_gateway/startup_resume.py`: `UPDATE_HOLD_QUEUE_REASON` joins `_RETRY_REASONS` with the 1 h bound.
- `tui_gateway/turn_marker.py`: `reason` on `record_turn_start`; `UPDATE_HOLD_QUEUE_REASON`,
  `UPDATE_HOLD_QUEUE_MAX_AGE_S`.
- The started turn's `_record_turn_marker` rewrites the entry without `reason`, and its terminal frame retires it. That
  is what keeps the accepted-before-restart path from replaying.

## Tests

`tests/tui_gateway/test_update_hold_submit_queue.py` (16 tests):

- Reply contract: `test_hold_reply_validates_as_prompt_submit_result`.
- Restart: `test_queued_hold_prompt_is_dispatched_after_the_update_restart`,
  `test_hold_prompt_older_than_the_turn_freshness_window_still_replays`,
  `test_hold_prompt_past_its_own_bound_is_dropped_and_an_ordinary_turn_keeps_15_min`,
  `test_startup_sweep_retries_hold_prompts_past_the_turn_freshness_window`,
  `test_replay_after_restart_takes_the_lease_over_a_stale_pre_restart_row_and_a_busy_state_db` (real Desktop lease
  admission).
- Accepted just before the restart (real claim/dispatch path): `test_prompt_accepted_just_before_the_restart_is_continued_not_replayed`,
  `test_prompt_answered_just_before_the_restart_is_not_sent_again`.
- Renderer reload: `test_renderer_reload_under_the_hold_leaves_the_prompt_to_the_live_queue` (through
  `_resume_reuse_live_locked`; failed before 0929), `test_renderer_gone_long_enough_to_reap_the_session_replays_once_in_the_same_backend`.
  The first test stubs `_start_session_work`, so it shows the guard holds with admission open; it does not show the
  race happening live. 0929's commit message says "under the update hold". The reachable window is the narrower one
  under Behavior.
- Cross-process probe (real `python -m tui_gateway.entry` processes, isolated HERMES_HOME, live code `4edd4010e0`,
  2026-10-29 00:37 PDT). It sets the hold and submits (`queued`, marker journaled), warm-resumes on the same backend
  (no `auto_continue`), and SIGTERMs. On a fresh backend it resumes: the replay is scheduled, the probe text is an
  active user row exactly once (accept row retired, replay row active), there is one `tui prompt accepted ...
  kind=user` line, and no `shown as not sent`. All 9 checks passed. The script is not in the fork; it is kept beside
  this incident's hand-over note.
- Stop: `test_stop_under_the_hold_discards_the_prompt_for_the_next_backend_too` (through the `session.interrupt` RPC).

Untested: the Desktop renderer's own handling of the `queued` reply (no Desktop test). The tests use stub
sessions; the full SIGTERM → respawn → resume run is proved live (see Gotchas).

## Applying

Apply 0900, 0901, 0929 in order after 0906 with `git am -3`. 0901 needs `7e8e8f4a59` (turn-lease busy-DB floor) for
its lease test.

## Gotchas

- The marker carries prompt text, unlike an ordinary turn marker. It is pruned like other markers and bounded at 1 h.
- A queued message whose hold outlasts 1 h is dropped at resume and shows as "not sent" again (the accept row is
  retired).
- Live proof: send a message while the drain window is up (hermes-sync log `core-drain: drain window ...`). After the
  restart, agent.log should show `prompt.submit ... queued behind the core-update admission hold`, then on the new
  backend a replay of that prompt as a user turn (`tui prompt accepted ... kind=user`). There should be no `shown as
  not sent` line for that session.
