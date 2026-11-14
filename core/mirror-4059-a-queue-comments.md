# Hold new queue-reason comments while preserving events

Mirror ordinals: 3778, 3779.

## Behavior

- Every meaningful queue-reason change still writes an event immediately, but a new thread comment normally waits until that meaning has held for 120 seconds and a later reply confirms it.
- First observation, return to origin and a new run remain immediate comments.
- Short-lived flaps that revert do not fill the thread; sustained structural changes/outages eventually become visible.

## How it works

`kanban_queue_reason` separates transition-event decisions from comment decisions. An origin-local `kanban_queue_commented` table stores the last commented observation/time and the current meaning's start time. Repeats write no event but may confirm a held comment. Each new meaning resets the hold; returning to the last commented meaning only comments on the existing volatile refresh policy. Legacy cards without the new row treat their last transition as already commented. The extra placement-refusal test patch opts that test out of waiting because it checks which reason is commented, not timing.

## Code

- `hermes_cli/kanban_queue_reason.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_queue_error_timing.py` — Timing-focused expectations use the new hold fixture where the assertion concerns error observations rather than elapsed comment confirmation.
- `tests/hermes_cli/test_kanban_queue_integration.py` — Integration fixtures opt out of the hold for existing comment-content assertions.
- `tests/hermes_cli/test_kanban_queue_reason.py` — Existing reason/comment assertions retain their content contract with hold timing isolated.
- `tests/hermes_cli/test_kanban_queue_reason_flaps.py` — Structural and eligibility/code changes append events immediately but comment only after a confirming held reply.
- `tests/hermes_cli/test_kanban_queue_reason_hold.py` — A recorded flood fixture is replayed with periodic confirmations; assertions bound total/recent comments while retaining latest reason/candidates and event coverage. Cases cover immediate first/new-run/origin comments, sustained hold timing and confirming text, transient reversion, hold restart, and sustained Board error plus recovery.
- `tests/hermes_cli/test_kanban_queue_safety.py` — Existing ownership/fence safety cases run with the hold disabled where immediate comments are incidental to their assertions.
- `tests/hermes_cli/test_kanban_queue_transient_debounce.py` — Transient observation/event debounce expectations are kept separate from the new comment hold.
- `tests/hermes_cli/test_kanban_worker_origin_repin_refusal.py` — The no-comment-hold fixture preserves the test of one pickup-gate comment across pool/origin refusal ticks.
- `tests/hermes_cli/test_kanban_worker_placement_race.py` — Placement race assertions keep immediate-comment fixtures so timing changes do not mask fence/race behavior.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

The hold is not a timer that comments by itself: another observation must confirm it. Event count and comment count deliberately diverge. Comment bookkeeping is local and is not a new synced Board table.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
