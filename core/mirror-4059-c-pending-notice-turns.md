# Pending notices share a boundary turn without crossing category or human input

Mirrored entries: 3871, 3872, 3873, 3879.

## Behavior

A starting automation/update-resume turn can carry queued completion/delegation/Kanban notices behind it up to the first human prompt, preserving their envelopes and admission order. Human prompts and finite/API/CLI single-input turns are not extended.

Only notices in the head’s notification category fold together, so a diagnostic-muted turn cannot silently consume a result notice. An unexpected fold failure does not fail the head. Tests also allow legitimate late session.info frames after a resume snapshot without weakening exactly-once text-delta assertions.

## How it works

`session_fold_pending` selects eligible foldable producers, validates each candidate’s route/owner/identity, refreshes its payload, and transfers queued custody to the head’s execution generation through the existing steer mechanism. Remote notices carrying kanban_event_keys keep their own turns. Settlement marks folded rows delivered only on completed; other outcomes requeue them in FIFO position. Update pause explicitly reverses transfers before checkpoint and the resumed turn can fold them again.

The follow-up filters by notification_category (default result). Unexpected scan errors run the head alone; check/transfer errors stop further folding, leaving untaken rows queued and ambiguous transfer fences available for settlement requeue. A released-notifier core-drain test expects one joined wake turn. The resume fixture records the TAIL watermark and bounds snapshot sequence between that point and the later head, allowing inventory frames published while the reply is assembled.

## Code

- `gateway/session_fold_pending.py`
- `gateway/session_ingress.py`
- `gateway/session_settlement_recovery.py`
- `gateway/session_update_pause.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/gateway/test_fold_pending_admissions.py`: Asserts update-resume carries a completion, Kanban/completion notices run as one ordered turn, completed rows settle once, failed/paused heads requeue and later deliver, and a queued human prompt is neither extended nor jumped. Follow-ups check diagnostic/result separation with mute flags and a fold error leaving both head and later result successfully completed.
- `tests/gateway/test_notifier_core_drain.py`: Checks human input bypasses the held notifier wakes and, after core drain releases, two wakes ride one combined automation turn.
- `tests/gateway/test_resume_live_text_snapshot.py`: Checks the resumed inflight text contains HEAD+TAIL, snapshot sequence covers TAIL but may precede a late session.info, and NEXT arrives exactly once; the event wait is extended without adding a fixed sleep.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Folding changes claim custody, not the original admission rows or admission-time fold rules. Do not mark a transferred row delivered on a failed or update-paused head. Unexpected-error tests do not enumerate every possible transfer-race outcome. The resume patch is test-only; it acknowledges correct asynchronous inventory behavior rather than changing the protocol.

## Plugin?

No. Admission transfer, settlement and update checkpointing must share the gateway’s authoritative execution generation.
