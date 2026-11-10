# Ready-card queue place and leftover claim release

Patches **2083, 2088**.

## Behavior

- `kanban_show`, `hermes kanban show` (text and `--json`) and the REST read all carry the same `queue` object plus a one-line `queue_note` for a ready card, so "ready, no run" no longer reads as stuck. Lanes: `local` (approx. cards ahead, running/limit), `worker_pool`, `machine` (labels in `runs_on` that another known machine carries), `unplaceable` (no known machine carries the labels; says to re-place it), `leftover_claim` (ready card still holding a local `claim_lock`).
- An unknown or unreadable machine registry never reads as unplaceable.
- The dispatcher writes one `skipped_unplaceable` event per unchanged condition when a label mismatch skips a card.
- Every dispatcher tick, `release_leftover_local_claims` clears this host's claim fields on ready/todo/review/blocked/scheduled/done cards whose worker is dead or gone, logging `claim_released` with reason `leftover_local_claim`. Live pids, claims in flight and cards leased elsewhere are skipped; status is unchanged.

## How it works

`kanban_queue_position.placement` reads `runs_on`, the sync registry and `effective_labels`; `position` returns it before the local-slot answer; `describe`, `read` (never raises), `fields`, `print_field` shape payloads. `eligible_rows` in `kanban_db_dispatch_preclaim` calls `note_unplaceable`, which appends the event only when the last event differs. 2088 moved `ORPHAN_CLAIM_GRACE_SECONDS` and `_claim_in_flight` into new `kanban_db_dispatch_claims.py` (re-exported), added the leftover sweep (exact-value compare-and-set UPDATE inside a write transaction) and wired it into the reclaim phase outside the "has running" gate. Root cause: a status-only sync write moved a card off running while claim columns, which are local and never overwritten by a pull, stayed set; the dispatcher claims only `claim_lock IS NULL` rows.

## Code

- `hermes_cli/kanban_queue_position.py`, `hermes_cli/kanban.py`, `tools/kanban_show.py`, `hermes_cli/kanban_db_dispatch_preclaim.py`.
- `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_claims.py` (new).

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/tools/test_kanban_show_queue.py`: tool and CLI JSON/text queue, non-ready card has none, unplaceable / other machine / satisfied locally / unknown registry, REST=tool=CLI parity, event written once. `tests/hermes_cli/test_kanban_leftover_local_claim.py`: dead-claim ready card released then claimable, a tick releases it with nothing running, each non-running status, live worker keeps claim, another host's lock not judged by a local pid, claim in flight left alone, running card untouched, card read names the claim. Desktop display of the note is untested.

## Applying

Apply ordinal(s) 2083, 2088 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. 2088 edits the file moved by 2083's queue module and extends it; keep both in order. Related: [kanban-queue-reasons-repin.md](kanban-queue-reasons-repin.md), [kanban-dispatch-ownership.md](kanban-dispatch-ownership.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- `ahead` is approximate (priority then age).
- Only host-local claim locks are judged; a pid means nothing on another machine.
- If a `leftover_claim` note persists, the claim belongs to a live or foreign worker, not a dead local one.

## Could this be a plugin?

No. Dispatcher, task schema and every card-read surface must agree.
