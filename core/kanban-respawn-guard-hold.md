# Respawn-guard hold notices and active_pr release

Patches:

- `patches/0212-kanban-respawn-guard-posts-comment-Inbox-card-within.patch`: a held card gets one comment + Inbox card within 30 min.
- `patches/0223-fix-kanban-explicit-check-False-on-respawn-guard-not.patch`: explicit `check=False` on the notice subprocesses (ruff PLW1510).
- `patches/0225-refactor-kanban-respawn-guard-hold-notice-lives-in-k.patch`: notice code moves to `kanban_db_dispatch_guard_notice.py`.
- `patches/0238-batch-pin-utf-8-decoding-in-dispatch-guard-gh-call-5.patch`: pin utf-8 decoding of the `gh` call output.
- `patches/0239-fix-kanban-respawn-guard-releases-active_pr-once-PRs.patch`: `active_pr` hold lifts once every named PR is merged/closed; no Inbox card; one event per hold.
- `patches/0241-refactor-kanban-move-active_pr-step-hold-event-dedup.patch`: PR step and hold-event dedupe move into guard siblings (code-health caps).
- `patches/0242-kanban-guard-hold-notice-file-a-fix-card-for-default.patch`: the notice files a fix card for the default assignee instead of posting to the Inbox.

Extends `kanban-park-invariants.md` and `kanban-pr-unblock.md` (the respawn guard itself).

## Behavior

- The respawn guard holds a `ready` card instead of spawning a worker (for example `active_pr`: the card's comments
  name a PR that is still being worked on, so a second worker would open a duplicate). Before this change a card could
  sit held for hours with nothing said.
- Once a hold has lasted past `HERMES_KANBAN_GUARD_HOLD_NOTIFY_SECONDS` (default 30 min), the dispatcher posts ONE kanban
  comment naming the guard reason and, for `active_pr`, the PR and a one-line summary of its failing checks. It also
  files ONE fix card for the default assignee (0212 first posted an Inbox card; 0239 dropped it; 0242 replaced it with
  a Kanban card because an unread Inbox poster gets muted). The notice does not restart the hold clock: the hold cap
  still fires.
- The `active_pr` hold lifts as soon as EVERY PR named in the card's comments is MERGED or CLOSED. An unknown state
  (any `gh` failure) counts as unfinished and the hold stays (fail closed).
- The dispatcher logs one `respawn_guarded` event per unbroken hold per reason, not one per tick (about 1400 events per
  card before). A human comment breaks the hold, so the next held tick logs a fresh event.

## How it works

`hermes_cli/kanban_db_dispatch.py` calls `guard_hold_reason` to find the hold in progress (newest event, ignoring the
dispatcher's own logs), then `_notify_guard_hold` in `kanban_db_dispatch_guard_notice.py` posts the comment and files the fix card
once per hold. `kanban_db_dispatch_pr_state.py` looks up each PR with `gh pr view` (`pr_state`, cached: merged/closed
forever, open for a short time) and `all_prs_finished(urls)` is true only for a non-empty set of finished PRs.

## Code

- `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_guard_notice.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`.

## Tests

- `tests/hermes_cli/test_kanban_park_invariants.py`: one comment (and fix card) naming the PR and failing checks within 30
  min, hold clock not restarted, the event re-logged after a human comment, PR-state table (merged/closed/open/unknown),
  merged-PR cache, end-to-end "a ready card whose only PR merged is spawned, not held".
- `tests/hermes_cli/conftest.py`: keeps the PR-state lookup offline by default (`gh` "fails", so `active_pr` holds).
- Not re-run when this doc was written (untested here). The `gh` helpers are only exercised through fakes.

## Applying

`git am -3` in order 0212, 0223, 0225, 0238, 0239, 0241, 0242 (the last four assume the `*_guard_notice` module from 0225).

## Gotchas

- Needs `gh` authenticated on the dispatcher host; without it holds simply never lift early.
- Patch 0242 was authored on a branch without 0239/0241, so the linear series would not apply it; the mirrored 0242 is the conflict-resolved form (it yields the same files as the fork's `main`).
- The fix card goes to the `default` profile; a board without that profile would drop it.

## Plugin?

No. Dispatcher behavior inside core.
