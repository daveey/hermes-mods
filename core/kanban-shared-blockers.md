# Kanban: park cards behind one shared external blocker and probe it once (patch 0141)

Patch:

- `patches/0141-feat-kanban-park-cards-behind-one-shared-external-bl.patch`: a new
  `hermes_cli/kanban_shared_blockers.py`, small hooks in `kanban_db.py`, `kanban_db_dispatch.py`,
  `kanban_continuation.py`, `kanban_ops.py`, `config_defaults.py`, `agent/prompt_builder.py`,
  `tools/kanban_tools.py` and `tools/kanban_tools_schemas.py`, plus
  `tests/hermes_cli/test_kanban_shared_blockers.py`.

What you see: when many cards hit the same wall outside any one card (GitHub Actions billing refused, a
provider outage, a release pipeline down), the board stops spending a worker run per card to rediscover it.
One card checks whether the wall is still there every 30 minutes, and the others wait. When the wall goes
away, the waiting cards go back to work by themselves, with no human unblocking each one. The motivating case
was 93 runs on 20 cards, each finding the same Actions billing refusal.

## Behavior

- `kanban_block` and `kanban_continue` take an optional `blocker_id`, a short slug such as
  `github-actions-billing`. Workers are told (tool schema + `KANBAN_GUIDANCE`) to set it only when the wall
  would stop other cards too, and to reuse an id listed under "Open shared blockers" in their context.
  The id is normalized to lowercase `[a-z0-9._-]`, at most 64 characters. An unusable value is refused with
  "blocker_id must be a short slug ...". The tool result echoes `blocker_id`.
- **Open blocker.** A card is behind blocker X while its latest closed run that says anything about the
  outside world carried X. X is open while its newest assertion is newer than its newest clearing.
- **One probe.** While X is open, the dispatcher's respawn guard holds every card behind X (guard reason
  `shared_blocker`, listed under `respawn_guarded` / "Guarded (shared_blocker)") unless:
  - no other card behind X is `running` (one probe at a time, always), and
  - `kanban.shared_blocker_probe_seconds` (default 1800) have passed since the last assertion or the last probe
    start, or a human unblocked this card after the newest assertion ("try again now").
- **The probe's context** says it is the probe for X: check whether the wall still holds, and if it does,
  end again with the same `blocker_id`. Every worker's context lists the open blockers with their parked-card
  counts and the newest reason.
- **Clearing.** A probe run that ends without X (it completed, handed off, or stopped for some other reason)
  clears X. On the next dispatcher tick, cards still `blocked` behind X are unblocked automatically. Each gets a
  `shared_blocker_cleared` event and a dispatcher comment naming the clearing card and run.
  `hermes kanban dispatch` prints "Released (shared blocker cleared): ..." (`shared_blocker_released` in
  `--json`). Held `ready` cards simply stop being guarded.
- **Neutral outcomes** (`crashed`, `timed_out`, `reclaimed`, `spawn_failed`, `rate_limited`, `gave_up`,
  `released`) say nothing about the wall, so they neither assert nor clear it.
- **No triage escalation for probes.** A probe re-blocking on the blocker it was parked behind would normally
  count toward the unblock-loop limit and go to triage. Here the card stays `blocked`, its recurrence count is
  kept, and the event carries `shared_blocker_probe: true`.
- Only blockers asserted in the last 14 days are considered.

## How it works

No schema change: the blocker state is derived from `task_runs` history each time it is needed.

- `block_task(..., blocker_id=)` writes `{"blocker_id": X}` into the closed run's metadata and the `blocked`
  event payload. It synthesizes a run when there is none, so an id is never lost. `continue_task` puts it into
  the continuation run's metadata and the `continued` payload.
- `kanban_shared_blockers.blocker_states(conn)` scans runs whose metadata mentions `blocker_id`, then walks each
  of those cards' runs in order to find which cards are still behind which blocker (`behind`), when each was
  asserted and cleared, and when probe runs started. Cards that are `done`/`archived` are never behind anything.
- `shared_blocker_guard(conn, task_id)` is called from `check_respawn_guard` as step 2b, right after the
  quota/auth blocker check. It returns `shared_blocker` or `None`, and it fails open (an exception means
  no guard).
- `release_cleared_blockers(conn)` runs in `_run_reclaim_phase` after `recompute_ready` and unblocks blocked
  cards asserted before the clearing.
- `render_blocker_context` adds the "## Open shared blockers on this board" section to `build_worker_context`.
  It is advisory, so errors are swallowed.
- Matching on normalized error text was considered and left out: unrelated crash errors share fixed preambles,
  and block reasons written by an LLM never match verbatim.

## Code

```python
# hermes_cli/kanban_db_dispatch.py, check_respawn_guard
from hermes_cli.kanban_shared_blockers import shared_blocker_guard
try:
    shared = shared_blocker_guard(conn, task_id, now=now)
except Exception:
    shared = None
if shared is not None:
    return shared
```

```yaml
kanban:
  shared_blocker_probe_seconds: 1800   # 0 = no spacing, still one probe at a time
```

## Tests

`tests/hermes_cli/test_kanban_shared_blockers.py` (7 tests, real temp board, dispatcher with stubbed spawns):

- `block_task` records the id on the run and the event.
- Three unblocked cards behind an open blocker get exactly one spawn per tick, and the others are guarded
  `shared_blocker`.
- A probe that completes clears the blocker, and the cards still blocked behind it are unblocked without a
  human.
- Neutral outcomes neither clear nor assert.
- A board with no blocker has no guard.
- The `kanban_continue` tool carries the id into the run metadata, and the probe's worker context names it.
- The `kanban_block` tool normalizes and echoes the id and rejects a non-string one.

```sh
scripts/run_tests.sh tests/hermes_cli/test_kanban_shared_blockers.py
```

Passed on the full 0001–0143 series at merge-base `4957bbd0a0` (Oct 19, 2026 PT). Untested: the 30-minute spacing
on a live board, and the human-unblock-lets-it-probe-now path (`_unblocked_since_assertion`), which no test
exercises.

## Applying

`git am -3 core/patches/0141-*.patch` after 0140 (both edit `tools/kanban_tools.py`). It also relies on
earlier fork Kanban patches: `kanban_continue` (`kanban-soft-deadline.md`, 0121), the block-kind routing and
unblock-loop triage (`kanban-block-loop-escalation.md`), and the `KANBAN_GUIDANCE` text from
`kanban-timed-waits.md`. Apply the series in order.

## Gotchas

- It works only if workers set `blocker_id`. A worker that blocks with prose only is not parked, and nothing
  infers the blocker from the reason text.
- Two workers that pick different slugs for the same wall make two blockers, each with its own probe. The
  "Open shared blockers" context lists existing ids to keep that rare.
- The probe has to say the wall is still there by passing the id again. A probe that blocks with some other
  reason clears the blocker and releases the parked cards. Usually that is right, because the wall is gone.
- State is recomputed from runs on every guard call. That is fine on normal boards, since the scan is bounded
  to 14 days and to runs whose metadata mentions `blocker_id`.

Plugin? No. The respawn guard, the block routing and worker-context assembly are dispatcher internals with no
hook points.
