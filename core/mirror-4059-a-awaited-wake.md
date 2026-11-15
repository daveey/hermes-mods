# Awaited-card wake and unreachable-owner regression contracts

Mirror ordinals: 3775, 3776, 3783.

## Behavior

- Test-only follow-ups pin lag-warning rearming when a park changes awaited references, reparks, or its awaited card reopens and finishes again.
- A task wait-check can fall back to fresh Board replica evidence when owner SSH raises timeout or OSError, not just when SSH returns a failure code.
- Resync/reconcile tests preserve changed-status wakes while avoiding unchanged-card re-probes and handling locked databases conservatively.

## How it works

These patches add tests around existing awaited-card and wait-check implementations. They use a stable park episode and control the selected finished reference to test warning identity separately from episode identity. Another case exercises expedite before the ordinary scan window. The parser-driven wait-check fixtures inject unreachable-owner exceptions and compare fresh replica status with unavailable/stale registry observations. Resync fixtures apply one feed page before a later retention exception and compare pre/post-rebuild status.

## Code

- `(test-only follow-up; production implementation is unchanged by these ordinals)`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_awaited_card_wake.py` — Assertions establish a second warning for a new awaited reference in one episode, expedite rearming after repark, status applied before Resync surviving through rebuild, no re-probe when status is unchanged, logged SQLite expedite error without aborting reconcile, warned marks retained with no replica, and marks dropped/rearmed after reopen/finish.
- `tests/hermes_cli/test_kanban_wait_check_board_replica.py` — The real CLI parser returns rc 0 with the Board-replica marker when fresh task evidence meets the probe despite owner timeout/OSError; absent Board evidence preserves the rc-1 registry answer, while an unreachable non-task probe returns rc 2 with an SSH error.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Only a task has the Board-replica fallback shown here. Warning records are diagnostic and must not themselves complete or reassign a parked card. These additions do not demonstrate production wake latency.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
