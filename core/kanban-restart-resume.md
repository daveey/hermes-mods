# Kanban: a run requeued by a gateway restart resumes the lost run's conversation

Patches: 1589 1590 1591

- `patches/1589-daveey-kanban-a-run-requeued-by-a-gateway-restart-re.patch`
- `patches/1590-daveey-kanban-restart-resume-code-health-fixes-narro.patch`
- `patches/1591-daveey-kanban-restart-resume-plan-and-copy-off-the-o.patch`

Related: [kanban-owner-restart-lost.md](kanban-owner-restart-lost.md) (how a run lost to its owner's restart is
booked `owner_restart_lost`, which is the trigger here).

## Behavior

- **Trigger.** When the run immediately before a new Kanban run ended `owner_restart_lost` (exit 76,
  `owner_restart`), the owning gateway copies that run's persisted active transcript into the new run's own fresh
  session before its first turn. The worker's prompt carries a resume note plus the current card state instead of
  the 16k-char interrupted-run transcript tail.
- **Ownership unchanged.** The new run still gets a new run id, claim, session id and `owner_admitted` marker; the
  old session is only read, never written.
- **Interrupted tool calls.** A trailing tool call with no recorded result gets a synthetic
  "interrupted by gateway restart" tool result so the conversation is well-formed.
- **Fallbacks** (start fresh with today's transcript tail, as before): `prior_session_missing`,
  `prior_session_empty`, `prior_session_unreadable`, `prior_session_rotated` (1591: the prior session ended by
  rotation compaction), `foreign_home`, `foreign_profile` (prior session in another home/state.db or profile),
  `over_compaction_threshold`, `threshold_unknown`, `plan_failed`, `seed_failed` (1591).
- **Size gate.** The compaction threshold comes from the same compression config parser and `ContextCompressor`
  the worker uses. 1591 resumes only below 85% of it (`RESUME_HEADROOM = 0.85`), leaving room for the first turn
  and for owner/worker context-length lookup differences.
- **Audit.** Every such run logs a `restart_resume` event carrying `prior_run_id`, `prior_session_id` and either
  `resumed_session_id` (also stamped on `owner_admitted`), `copied_messages`, `interrupted_calls`, or the
  `fallback` reason (plus `messages`/`tokens`/`threshold` when known).
- **Other requeues unchanged.** Crash and timeout requeues, and a crash after a restart loss, are not resumed (only
  the run immediately before counts).
- **1590 (code health).** The custom-provider lookup in `compaction_threshold_tokens` no longer hides errors behind
  a broad `except Exception`; `build_worker_context(..., recovery_tail=False)` is shortened to keep `kanban_db.py`
  under its size cap; the test fallback cases became a table. No behavior change.
- **1591 (off-loop, fail-soft).** Planning (transcript read, token estimate, context-length lookup that may probe
  over HTTP) moved into the tracked off-loop write together with the copy, after session creation, so the owner
  event loop never blocks on it and the saved-claim/create invariant is untouched. Any exception is a recorded
  fallback (`plan_failed` / `seed_failed`), never an admission failure.

## How it works

`gateway/session_kanban.py` asks `restart_candidate(conn, task_id, run_id)` whether the previous run ended
`owner_restart_lost`. If so, after creating the new run's session, it runs `plan_and_seed` off the loop:
`plan_resume` validates the prior session (source/profile/home, `end_reason`, message count, token estimate vs the
threshold) and returns either a copy plan or `{'fallback': reason}`; `seed_resumed_session` copies the messages
idempotently (a retried admission does not double-seed), and `record_resume_event` writes the `restart_resume`
event. The worker turn then uses `worker_prompt_context`, which swaps the context for `resume_context(...)` only when
history was actually restored; `build_worker_context(..., recovery_tail=False)` omits the transcript tail then.

## Code

`gateway/session_kanban_resume.py` (new), `gateway/session_kanban.py`, `hermes_cli/kanban_db.py`
(`build_worker_context(recovery_tail=...)`), `website/docs/user-guide/features/kanban.md` (restart-resume
paragraph and the `restart_resume` event row).

## Tests

`tests/gateway/test_session_kanban_restart_resume.py` (state at 1591):
`test_requeue_after_owner_restart_resumes_the_prior_session`,
`test_a_mid_tool_call_turn_gets_a_synthetic_interrupted_result`,
`test_each_fallback_keeps_the_tail_only_fresh_session` (parametrized over nine cases: missing, empty, foreign_home,
foreign_profile, unreadable, over_threshold, and the 1591 cases plan_raises, rotated, seed_raises),
`test_a_non_restart_requeue_is_unchanged` (parametrized over `crash` and `timeout`),
`test_a_crash_after_a_restart_loss_is_not_resumed`, `test_seeding_is_idempotent_for_a_retried_admission`,
`test_compaction_threshold_comes_from_the_compressor_config`,
`test_the_worker_turn_uses_the_resume_context_only_with_restored_history`.
These were not rerun by the mirror pass. Untested: the `threshold_unknown` fallback, requeue kinds other than
crash/timeout, the exact 85% headroom boundary, and a live gateway restart end to end.

## Applying

Apply 1589, 1590, 1591 in order (1590 and 1591 edit the file 1589 adds). No config. Depends on the
`owner_restart_lost` booking from the patches in [kanban-owner-restart-lost.md](kanban-owner-restart-lost.md).

## Gotchas

- Resume needs the prior session in the same home/state.db and profile; a run moved to another profile or host
  falls back to the tail.
- A prior session rotated by compaction is never resumed (its active transcript is already a summary).

## Plugin?

No. The hook point is inside the gateway's Kanban run admission (session creation and the worker's first-turn
context), which no plugin hook exposes.
