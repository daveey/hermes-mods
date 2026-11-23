# Startup resume retries and update-marker deferral (mirror 5447, 5451–5454)

## Behavior
- The post-boot observer (`_observe_pauses` renamed `_observe_markers`) now watches every marker with `auto_continue` (previously only update-pause retry reasons; ordinary crash markers were boot-only) and re-runs `resume_interrupted_sessions` without `paused_only`. Recovery still goes through the normal `session.resume` admission path; this patch does not change that path's guards, and the diff does not show them.
- An automatic turn that is refused because a core update still holds the paused turn's live writer is deferred silently (existing retry/backoff) instead of emitting an ownership/takeover card.
- Deferral applies only when the registry holder's recorded PID/birth matches a paused marker, the same-install spawn ledger, a live updater incarnation, and real POSIX checkout kernel-lock contention. The marker incarnation is bound to the identity record read from the very descriptor used for the contention probe (matching PID/birth and a valid update timestamp).
- Fail closed (original refusal and card kept): refusal codes other than `SESSION_NOT_OWNED`, unknown holder PID/birth, a marker that is not `core_update_pause` or whose writer PID/start time does not match or is not alive, a missing/unverified ledger row, a missing or dead updater incarnation, no kernel-lock contention, a non-POSIX platform, a writer that changes during the probe, and a PID-only lease holder string. The classifier is documented as never being admission or lease-takeover authority.

## How it works
`tui_gateway/startup_resume.py` observes all eligible markers (private helpers renamed). `tui_gateway/update_marker_owner.py` classifies the holder; `hermes_cli/active_sessions.py` registry refusals now carry the recorded holder PID/birth (and `ownership_cause = lease_foreign` when that incarnation is live); `tui_gateway/prompt_turn.py` and `session_lifecycle.py` suppress the card for the classified case.

## Code
`tui_gateway/startup_resume.py`, `tui_gateway/update_marker_owner.py`, `tui_gateway/prompt_turn.py`, `tui_gateway/session_lifecycle.py`, `hermes_cli/active_sessions.py`.

## Tests
Source: `tests/tui_gateway/test_startup_resume.py` (adds `test_lifetime_observer_retries_an_ordinary_interrupted_marker`); `test_startup_resume_late_pause.py`, `test_startup_resume_observer_guards.py`, `test_update_hold_submit_queue.py` and `test_update_queue_recovery.py` are touched but the diff adds no named test functions to them, so their changes are not described here; `test_update_marker_defer.py` (new; by test names: classification of proven refusals, registry producer, automatic-turn deferral and retry/recovery, other refusals keep their card, unproven markers never relabel, a real other-process marker defers, registry carries original holder birth, no marker probe for other refusal codes or unknown holder identity, writer dying during the probe, updater must match the held checkout record, unsupported-platform and unknown-incarnation controls). Not executed in this mirror pass.

## Applying
Apply in ordinal order; later patches add tests and the incarnation binding.

## Gotchas
Deferral is silent (an info log only) and only for turns marked `_synthesized_turn`; a non-POSIX platform or any unproven custody keeps the takeover/ownership card.

## plugin?
No.
