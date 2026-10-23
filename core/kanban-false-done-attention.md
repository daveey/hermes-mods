# Done-but-not-done kanban work stripes amber (patch 0194)

Patch:

- `patches/0194-feat-sidebar-amber-attention-stripe-for-done-kanban-.patch`: `register_done_check` +
  `attention_task_counts` in `hermes_cli/kanban_session_activity.py` (stamped as `kanban_attention` by
  `annotate_kanban_activity`), the Desktop `attention` stripe (`store/session-dot-state.ts`,
  `lib/session-status-stripe.ts`, `session-row.tsx`, `session-tab-accent.tsx`, `sidebar-visible-order.ts`,
  `sidebar-automated.ts`, `lib/session-signatures.ts`, `types/hermes.ts`), one i18n key (`sidebar.row.kanbanAttention`,
  English only, other locales in `overlay-gaps.json`), and tests.

What you see: a chat whose kanban cards are all done used to go green even when a card's handoff said part of the
work was dropped ("…split to follow-up", "I could not run it on the iMac") and nobody made the follow-up card. Such a
chat's row and tab now get an **amber** stripe ("needs attention") and an amber warning count after the age; its
tooltip says how many done cards dropped work with no follow-up card.

This extends the sidebar status stripe (`sidebar-status-stripe.md`) and the kanban counts on rows
(`kanban-blocked-sidebar.md`).

## Behavior

- Core decides nothing about wording. A plugin registers `register_done_check(name, fn)`; `fn(card) -> bool` gets
  every card with status `done` completed in the last 24 h (`ATTENTION_WINDOW_S`) that a session created
  (`tasks.session_id`) or shepherds (`tui` notify subscription). `card` has `id`, `title`, `summary` (latest run
  summary, else `result`), `completed_at`, `board`, `board_path`. True = suspect.
- No check registered (upstream, or the plugin disabled) → no `kanban_attention` field and no extra reads.
- A check that raises counts as False. Counts are cached 30 s; registering or disposing a check drops the cache.
- `kanban_attention` is summed over the row's compression lineage and removed when zero, like `kanban_active`.
- `sessionStatusStripe(..., reviewWait, kanbanAttention)` gains a 7th argument. It only replaces `done` (idle with
  turns, or unread) with `attention`. Working, needs-input/stalled, blocked kanban (red), ⌘B and periodic keep
  their own stripe. ⌘] / ⌘[ stop on amber rows. Automated (bot) rows with amber stay visible like red ones.
- The amber clears when the window passes or the check stops flagging the card. With `plugins/kanban-honest-done`:
  a follow-up card created by or linked to it exists, or someone comments `honest-done: ack` on the card.

## How it works

```python
# hermes_cli/kanban_session_activity.py
def register_done_check(name, fn) -> Callable[[], None]   # returns a disposer
def attention_task_counts(now=None) -> dict[str, int]     # {session_id: flagged done cards}
```

`_read_recent_done(path, since)` reads `(session_id, card)` pairs per board (created + `tui` subscribed);
each `(board, task)` is judged once per refresh. `annotate_kanban_activity` stamps the count with the other
kanban fields, so every session-list endpoint that already calls it carries `kanban_attention`.

## Tests

- `tests/hermes_cli/test_kanban_session_attention.py`: no check → no field; flagged cards stamp the creating,
  subscribed and lineage rows across two boards (run-summary and `result` fallback), skip clean, old (> 24 h) and
  running cards, clear stale stamps; a raising check counts as clean; the disposer unregisters. RED (2 of 3) on the
  module without the patch.
- `apps/desktop/src/store/session-dot-state.test.ts` ("paints attention (amber) instead of done…"): the stripe
  priority table above.
- `apps/desktop/src/app/chat/sidebar/session-row.test.tsx` ("paints the amber (attention) stripe…", "stays green
  (done) without kanban_attention"): rendered row stripe + warning count + label.
- `apps/desktop/src/store/sidebar-automated.test.ts`: an amber bot row stays visible.
- Untested: the tab underline (`session-tab-accent.tsx`) and ⌘]/⌘[ stepping onto an amber row (one-line changes
  sharing the tested predicate).

## Applying

Apply after 0143 (review-wait stripe: same `sessionStatusStripe` signature) and the kanban counts patches. Then
install a check: `plugins/kanban-honest-done` registers one from its `register()` (feature-detected, so it loads
harmlessly on cores without this patch).

## Gotchas

- The check runs inside the Desktop backend's session-list call (cached 30 s). Keep it cheap: regex first, board
  reads only for cards that match.
- A long-running backend imported the plugin before this patch landed has no registry; the amber appears after
  the backend restarts on the updated core.

## Plugin?

Core: no plugin hook can add a field to session-list rows or a stripe colour. The judgement itself lives in a plugin.
