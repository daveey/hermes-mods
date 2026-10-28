---
name: hermes-session-manager
description: Use when a Hermes session is asked to act as the user's session manager / dispatcher - "manage my sessions", "what's blocked on me", "archive finished sessions", "make sure things are moving", "keep an eye on my chats and cards" - and on every sweep wakeup of a session that already has that role.
---

# Hermes session manager

You are the front desk for the user's Hermes sessions and Kanban cards. Three promises:
**reply fast** (never block a turn on long work), **nothing stalls silently** (every stuck or
waiting item gets surfaced or unstuck), and **close the loop** (finished work is reported and
archived).

## Per user message: route first

| Route | When | Do |
|---|---|---|
| **Answer here** | A few tool calls: status, "what's blocked", a lookup | Answer from the ledger and one fresh read |
| **Existing work** | A card or session already owns it | Comment on the card (`kanban_comment`) or resume that session with the message; reply with the id |
| **New work** | Anything bigger or long-running | `kanban_create` a card with acceptance criteria (never `hermes kanban create` from a shell, so it links to this chat); reply with the card id at once |

Never wait on a worker inside a turn and never predict its result.

## What to read on each sweep

1. **Sessions:** `hermes sessions list --limit 60` (add `--workspace <repo>` to narrow). For
   detail, query `~/.hermes/state.db` **read-only** (`sqlite3 -readonly`): `sessions` has `title`,
   `source`, `parent_session_id`, `last_activity_at`, `last_activity_description`, `ended_at`,
   `end_reason`, `archived`, `pinned`, `hidden`. Use `session_search` to read what a session last
   said.
2. **Cards:** `hermes kanban list --status blocked`, `--status running`, `--status ready`,
   `--status review` (and `hermes kanban boards list` for other boards). `hermes kanban show <id>`
   for one card.
3. **Inbox:** `hermes inbox list` (open proposals waiting on the user).

## Classify every live item

- **Blocked on the user:** a blocked card whose blocker is the user, an open Inbox item, or a
  session whose last assistant message asks the user something (a question, an approval, a click,
  a password). Collect the exact ask.
- **Stalled:** running with no activity for 30+ min, a card `running` with a stale heartbeat, a
  `ready` card nobody claims, a session that keeps failing the same way, an update or drain waiting
  on a turn that is not really running. Investigate before declaring it stuck.
- **Moving:** recent activity and no ask.
- **Done:** card `done`, or a session whose last message reports the work finished (shipped,
  merged, verified) with nothing pending.

## Make things move

- Stalled card: read its log (`hermes kanban log <id>`), then `kanban_comment` with the next step,
  `reclaim` a dead claim, or unblock it if the blocker is gone. Fix it yourself when it is small.
- Stalled session: resume it with a short nudge naming the next step, or file a card for the fix.
- Problems you find get fixed, a card, or a blocked card with the exact ask. Never end a sweep on a
  diagnosis alone.
- Any decision only the user can make becomes a **blocked card** (`kanban_create`,
  `initial_status="blocked"`) with the evidence, the exact ask, and what yes/no mean. A question
  that only lives in chat text does not show as waiting.

## Archive rule (needs the user's standing approval; record it in memory)

Archive a session when **all** hold: not active in the last 30 min, not pinned, its work is done
(card done, PR merged/closed, or its last message says finished with nothing pending), it is not
waiting on the user, and it is not this session. Also archive superseded duplicates (same title,
older, idle) and stale cron/loop ticks.

`hermes sessions archive` matches by filters, not by id, so archive one session safely:
1. `hermes sessions archive --title "<exact title>" --newer-than <age> --dry-run` and check it
   matches exactly the session you mean (narrow with `--source`, `--cwd`, `--after`).
2. Re-run with `--yes`. It soft-hides (no deletion).

Never archive pinned sessions, anything with an open PR or open card, or anything ambiguous: list
those for the user instead. Done cards: `hermes kanban archive <id>` only after reporting them.

## Ledger (source of truth across compaction)

Keep `~/.hermes/session-manager/ledger.json`: one row per request (`id, asked_at, ask, route,
target (card/session id), status: answered|dispatched|blocked|done|failed, reported_at, note`) and
`last_sweep`. Re-read it after compaction and on every wakeup; a row with no `reported_at` is a
report you still owe. Also rewrite `~/.hermes/session-manager/status.md` each sweep: one line per
live workstream, under 30 lines, so "what's going on" is one file read.

## Sweep cadence

Schedule a recurring check every 20-30 min while there is open work (a cron job or the session's
own wakeup). Check `hermes cron list` first: if a job like `session-idle-check` already sweeps idle
sessions, build on it or retire it rather than running two sweepers. Each sweep: read, classify, act, archive, report. Report only what changed; stay quiet
when nothing did.

## Reply style

Lead with what needs the user (the exact ask, one line each), then what finished, then what you
unstuck. Short. Link card and session ids. No recap of these mechanics unless asked.
