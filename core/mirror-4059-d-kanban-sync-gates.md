# Kanban notifier, overlap, sync, and refusal regression contracts

Mirror snapshot 4059; covered ordinals: 3907, 3910, 3916, 3917, 3929, 3932, 3944, 3945.

## Behavior

These are test-only follow-ups. They pin conservative notifier/sweep gating, same-family overlap exemptions, resync preservation, exact crash/refusal custody, and cold-rebuild repair. They also tighten test-fixture reads of a concurrently appended timing log. No new production behavior is introduced by these ordinals.

## How it works

Tests exercise real board writes and existing gate/classifier APIs rather than inferring safety from commit titles. Notifier skips need a revision-backed key and subscription-specific pending kinds. Held resync evidence survives a concurrent outbox acknowledgment. A proven midrun owner refusal is distinguished from ordinary output/failure using the selected bound result receipt. A successful cached sweep cannot mask unreadable due times or a subsequent failed rerun. A cold-live rebuild forces capture-gap repair even within a prior repair window; the warm control waits for the window.

The timing helper only parses newline-terminated records, discarding an incomplete final JSON record while the writer is active.

## Code

These ordinals change only the test files listed below; the existing implementation boundaries are identified under How it works.

### Mirrored commits

- 3907: `161cd13a678a1e1ff2be8f6f6d1d63f723ecbe5e` — Pins conservative notifier keys and TUI pending-kind filtering.
- 3910: `4dc17201a9c853a3618a7a58f715e22a1089bf6b` — Pins same-family overlap creator and unknown-time exemptions.
- 3916: `f4208d28100e4bea0087e938ca466a7d85568d76` — Pins resync held-card protection and initial link sweep bounds.
- 3917: `7d7074c52f8bc192c42610895e9e9836b063e09d` — Pins stop prefixes, ambiguous PR SHA hints, and negated duplicates.
- 3929: `bea95711b658728202b132f17b1c8ed03b0d8337` — Reads only complete records from the live sync timing log fixture.
- 3932: `8c8aafea9da2e1d07f4dd0e56ef248a5cd6199bd` — Pins bound legacy/modern midrun owner-refusal receipt proof.
- 3944: `9ae5a2f1cf3ed0729d83d86024764f9c3fd07fb3` — Pins completed sweep-key failure invalidation and unreadable due reads.
- 3945: `15b0c1364782908f831ae4c43dd48f8a9a55473e` — Pins cold rebuild repair despite an earlier completed repair window.

## Tests

- `tests/gateway/test_kanban_notifier_board_gate.py`: dropping activity triggers prevents skip reuse on every tick; a TUI-only unsupported event kind permits idle skips but a supported completion reaches the claim.
- `tests/tools/test_kanban_create_overlap_same_creator.py`: an earlier same-family card loses exemption only for its own creator; missing `created_at` is not evidence of an earlier run.
- `tests/hermes_cli/test_kanban_sync_live_resync_sweep_held.py`: task, child-row, and child-link outbox operations name cards whose absent rows must survive acknowledgment mid-rebuild; links created later remain outside the initial sweep bound.
- `tests/gateway/test_session_cron.py`: only stop-class prefixes classify as expected stops; mere mention inside a real failure does not.
- `tests/hermes_cli/test_kanban_ci_progress_pr.py`: a ref containing two SHAs does not fall through to an unrelated single-SHA hint.
- `tests/hermes_cli/test_kanban_dup_detector.py`: hedged negation still denies a duplicate judgment without the optional word `as`.
- `tests/hermes_cli/test_kanban_sync_timing_loop.py`: the live-log helper now excludes an unterminated record before JSON parsing; this is test-harness synchronization, not a runtime parser change.
- `tests/hermes_cli/test_kanban_owner_unreachable_midrun.py`: wrong legacy PID/claim or nonfailure exit is not proof; modern bound receipt sequence/identity chooses the terminal envelope. Proven owner unavailability requeues without failure budget or gave-up event, whereas ordinary turn text consumes failure budget and emits no owner notice.
- `tests/hermes_cli/test_kanban_sweep_gate.py`: an unreadable due query reruns an already completed key; a failed due rerun invalidates prior completion until a successful retry.
- `tests/hermes_cli/test_kanban_sync_cold_live.py`: a capture-gap edge is published immediately during cold rebuild even after a completed pass; a warm control defers until the configured repair window.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

The non-revision notifier test establishes the documented no-skip rule, not a demonstrated missed write under the alternative key. A replica result alone is not authoritative refusal evidence. Tests using interrupted or partial records must synchronize on complete observations rather than fixed delays.

## Plugin?

No. These tests protect existing core database, notifier, sync, and worker-recovery contracts.
