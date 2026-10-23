# A recovered quota wall never becomes a human block

Patches:

- `patches/0248-fix-kanban-a-recovered-quota-wall-never-becomes-a-hu.patch`: quota/429 text takes the cooldown path whatever the latest run outcome.

Extends `kanban-shared-blockers.md` and `kanban-park-invariants.md`.

## Behavior

Cards held by the respawn guard after a rate-limit requeue used to end up blocked as `capability` (a human kind) hours after
the quota wall had cleared. Now:

- Error text that looks like quota/rate-limit/429 (and carries no auth signal) takes the `rate_limit_cooldown` path no
  matter what the card's latest run outcome is. When the cooldown elapses the failure stamp is cleared and the card retries.
- The guard-hold cap, on a quota-flavored error or an active cooldown, re-queues the card (clears the stamp) instead of
  blocking. Real auth errors still block as `capability`.
- On each dispatcher tick, existing guard-hold `capability`/`needs_input` blocks whose error is a quota error are released
  once another card completed after the block, with a comment saying so.

## How it works

The rate-limit requeue stamps `last_failure_error`. A later non-failure run (`changes_requested`, `blocked`) became the
latest run, so the `outcome=rate_limited` branch stopped firing and the stamp matched the respawn-guard's blocker regex as
`blocker_auth`. `hermes_cli/kanban_db_dispatch_quota.py` (new) holds the quota classification and release step used by
`kanban_db_dispatch.py`; `website/docs/user-guide/features/kanban.md` documents the behavior.

## Code

- `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_quota.py`, `website/docs/user-guide/features/kanban.md`.

## Tests

- `tests/hermes_cli/test_kanban_quota_guard.py`: a stale quota stamp is not `blocker_auth` (any latest outcome, or no runs); the rate-limited outcome clears the stamp after the cooldown; a real auth error still escalates and auth words win over quota words; a quota hold never escalates to a human block; the hold cap re-queues a quota error; an existing quota-hold block is released after another card completed (a completion before the block is no proof; an auth hold block is never released).
- `tests/hermes_cli/test_kanban_list_undecodable_text.py`: its BLOB fixture now uses auth text (`401 unauthorized`), since quota text no longer matches the blocker pattern.
- Not re-run when this doc was written (untested here).

## Applying

`git am -3` after 0239-0242 (it shares the guard code).

## Gotchas

- "Another card completed after the block" is the recovery signal; a wall that lifts with no other card finishing keeps the block until then.

## Plugin?

No.
