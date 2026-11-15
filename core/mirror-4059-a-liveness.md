# Worker-liveness evidence regression pins

Mirror ordinals: 3765.

## Behavior

- Test-only cases pin failed-result observation to a nonzero modern worker exit.
- An older final result cannot override a newer live runtime binding.
- Verified death of every recorded process can establish worker exit even without a final-result event.

## How it works

The patch drives existing failure-shape observation and worker-exit/notifier paths with isolated evidence changes. Strict death-proof metadata is distinguished from success/legacy outcomes; binding recency orders final-result evidence; local claim/process identities supply the final no-result death case. It changes tests only, not the runtime decisions.

## Code

- `(test-only follow-up; production implementation is unchanged by these ordinals)`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_liveness_guard_pins.py` — Assertions verify the failed observer is not armed by inapplicable exits and is armed for the targeted nonzero modern run; a newer live binding suppresses worker_exited notification; both-alive and outer-alive controls remain live until every recorded process is dead, after which exit delivery is asserted without any worker_result event.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

No process-result event alone is universal death evidence. This documentation records tested contracts, not a live worker diagnosis or new recovery mechanism.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
