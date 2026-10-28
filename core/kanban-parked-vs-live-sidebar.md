# Sidebar tells parked kanban work from running work

Patch **0510** (source `cde62e1cf4`).

## Behavior

A chat that handed work to kanban cards used to paint the same grey "waiting" stripe whether a worker was running
its card or the card was parked until Thursday. On 2026-10-27, 26 sidebar rows looked busy while one card ran.

The stripe now reads the cards' real state:

| Open cards of the chat | Stripe |
|---|---|
| any live card: `running`, `ready`, `review`, a `todo` whose parents all finished, an unverified remote card | none (working) |
| only parked cards: `scheduled`, `blocked`/`triage` on a non-human kind, `todo` behind an unfinished parent | grey "waiting" |
| a `todo` queued behind a card blocked on a validated human request (held) | red, like a blocked card |

`kanban_active` is unchanged: it still counts every open card, so the archive guard and "not done" logic see parked
work too.

## How it works

- `hermes_cli/kanban_session_activity.py`
  - `_read_parked(path)` reuses the counted (session, task) pairs and classifies each card. A `todo` with an open
    parent (`task_links`) is parked. If any ancestor is `blocked`/`triage` with `kanban_needs_human`, it is held.
    Boards without `task_links` classify by status alone.
  - Replica (remote) cards have no parent links. The owner promotes a ready `todo` on its next tick, so a
    replicated `todo` counts as parked. Remote cards are never held.
  - Filled in the same `_all_counts` pass as the other counts (thread-local `_parked_sink`, cached in
    `_parked_cache`). `annotate_kanban_activity` reads that snapshot, never a second projection.
  - Rows get `kanban_parked`, stamped even when 0 if `kanban_active` is set, so its absence means an older backend.
    They also get `kanban_held`, absent when 0. A failed read drops both together with the other counts.
- `apps/desktop/src/store/session-dot-state.ts`
  - `liveKanbanCount(row)` is `active - parked - held`, or `active` when `kanban_parked` is absent.
  - `withRowActivity` paints the dot `background` only for live cards.
  - `kanbanStripeCounts(row)` returns `{blocked: kanban_blocked + kanban_held, waiting: parked if nothing is live
    else 0}`. Every `sessionStatusStripe` caller uses it: `session-row.tsx`, `session-tab-accent.tsx`,
    `sidebar-visible-order.ts` and `sidebar-automated.ts`.

## Tests

- `tests/hermes_cli/test_kanban_session_activity.py`:
  - `test_parked_cards_are_counted_apart_from_live_ones`
  - `test_todo_behind_a_human_blocked_ancestor_is_held_not_parked`
  - `test_board_without_links_table_still_classifies_scheduled`
  - `test_replica_scheduled_and_todo_cards_count_as_parked`
  - `test_failed_read_drops_parked_and_held`
- `tests/hermes_cli/test_kanban_annotation_snapshot.py` (existing) guards the one-projection invariant.
- `apps/desktop/src/store/session-dot-state.test.ts`, `describe('kanbanStripeCounts')`:
  - live → no stripe;
  - parked-only → grey, dot not running;
  - held → red;
  - held plus a live turn → no stripe;
  - an older backend keeps the old behavior.

## Applying

`git am -3` 0510. The published patch is adapted to the linear series: it keeps patch 0364's field order and adds `kanban_held | kanban_parked`. It does not adopt the fork merge order of those fields. It touches only the row stamp and the stripe inputs. If `sessionStatusStripe` gained more call
sites, route their kanban arguments through `kanbanStripeCounts`.

## Gotchas

- A `ready` card that stays `ready` reads as working. That is a dispatch stall, not a parked card: count it as a
  false stop (see `~/.hermes/scripts/false_stop_monitor.py` on David's hosts).
- Remote held needs parent links in the hub replica. Until the replica carries them, a remote queue behind a
  human ask reads grey, not red.
