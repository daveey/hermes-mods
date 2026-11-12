# Unread Kanban cards never derive a chat outcome of done

Patches: **2435, 2436, 2437, 2438**.

## Behavior

When deriving a chat session's outcome (`hermes_state_outcome`), a failed read of Kanban cards (for example a board lifecycle lock while updates are paused) is treated as unknown, not as "no work". Previously the failure was swallowed as empty, so a closing-looking reply could be painted `done` while a card was still open. Now: a failed card read yields `unresolved` with reason `card_read_failed`; a stored `waiting` or `decision` outcome is kept as is; a declared `done` and a silent-cron completion cannot outrank an unread card (the declared outcome is carried as `declared` on the unresolved result); a declared `waiting` and provider decisions still hold. Replies that merely mention a card (including "No remaining work" or a historical "not yet running") are not demoted.

## How it works

2435 first added a prose heuristic (`_names_unverified_card`: a card id plus an unfinished cue word) and a shared per-sid failure set. 2436 removed both because keyword co-occurrence misfires on negation and tense, and replaced the shared marker with an explicit flag returned from `_live_work`, so concurrent derivations for the same sid cannot interfere. 2437 moved the failed-read guard ahead of the declared-`done` shortcut in `_derive_current`. 2438 kept `_card_items` as a `(decisions, waits)` 2-tuple (other callers depend on it); `_live_work` detects a failed read with a per-invocation sentinel passed as `_guarded`'s fallback and returns `(decisions, waits, card_read_failed)`. Net state after 2438 is the guard, not the heuristic.

## Code

`hermes_state_outcome.py` (`_card_items`, `_guarded`, `_live_work`, `_derive_current`).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_state/test_session_outcome.py` (internal `derive_outcome`/`refresh_outcome` API: busy card read with each prior kind, same-sid interleaving, declared/provider/silent-cron precedence, counted-card waiting, truly-done replies). The 2438 commit message reports other test files importing `hermes_state_outcome` (e.g. under `tests/tui_gateway/`) as the reason for keeping the 2-tuple; those are not specifically claimed here. No tool, CLI, or REST path is tested by these patches.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The final guard is deliberately narrow: only a failed read is distrusted. A card that reads successfully but is missing from the projection is not detected here.
- 2435 alone is not the final behavior; applying only part of the series leaves the heuristic in place.
- `_guarded`'s fallback is now explicit per source; other sources (processes, subagents, goals) still count as empty on failure.

## Plugin

Core change; no plugin required.
