---
name: hermes-session-manager
description: Use when a Hermes session is asked to act as the user's session manager / dispatcher - "manage my sessions", "what's blocked on me", "archive finished sessions", "make sure things are moving", "keep an eye on my chats and cards" - and on every sweep wakeup of a session that already has that role.
---

# Hermes session manager

Route quickly, recover stalled work, and verify closeout in the originating session.

## Per user message: route first

| Route | When | Do |
|---|---|---|
| **Answer here** | A few tool calls: status, "what's blocked", a lookup | Answer from the ledger and one fresh read |
| **Existing work** | A card or session already owns it | Comment on the card (`kanban_comment`) or resume that session with the message; reply with the id |
| **New work** | Anything bigger or long-running | `kanban_create` a card with acceptance criteria (never `hermes kanban create` from a shell, so it links to this chat); reply with the card id at once |

Never wait on a worker inside a turn and never predict its result.

## What to read on each sweep

1. **Sessions:** `hermes sessions outcome-audit --json` first (see Classify), then `hermes sessions list --limit 60` (add `--workspace <repo>` to narrow). For
   detail, query `~/.hermes/state.db` **read-only** (`sqlite3 -readonly`): `sessions` has `title`,
   `source`, `parent_session_id`, `last_activity_at`, `last_activity_description`, `ended_at`,
   `end_reason`, `archived`, `pinned`, `hidden`. Use `session_search` to read what a session last
   said.
2. **Cards:** `hermes kanban list --status blocked`, `--status running`, `--status ready`,
   `--status review` (and `hermes kanban boards list` for other boards). `hermes kanban show <id>`
   for one card.
3. **Inbox:** `hermes inbox list` (open proposals waiting on the user).

## Classify every live item

**Outcome first.** Read each chat's stored outcome before anything else:
`hermes sessions outcome-audit --json` (every sidebar row as `{id, title, kind, reason, age_h, on, items}`), or
`hermes sessions outcome <id> --json` for one. Map it directly: `decision` → blocked on the user (its `items`
are the asks), `waiting`/`running` → inspect the named work, `done` → done (`summary`), `muted` → leave
alone, `unresolved` → stalled, with `reason`. Stored waiting/running is not execution evidence. After recovery
or ownership changes, refresh this authoritative outcome; an immediate declared-done readback can precede
link derivation and revert to waiting. Use the fallback rules below only where no authoritative outcome exists.

- **Blocked on the user:** a blocked card whose blocker is the user, an open Inbox item, or a
  session whose last assistant message asks the user something (a question, an approval, a click,
  a password). Collect the exact ask.
- **Stalled:** running with no activity for 30+ min, a card `running` with a stale heartbeat, a
  `ready` card nobody claims, a session that keeps failing the same way, an update or drain waiting
  on a turn that is not really running. Investigate before declaring it stuck.
- **Moving:** recent activity and no ask.
- **Done:** card `done`, or a session whose last message reports the work finished (shipped,
  merged, verified) with nothing pending.

**Never report stale state.** The user also talks to sessions and cards directly. Before listing
anything as blocked on the user, re-read that card or session live in the same turn and confirm
the ask is still open. If you can't confirm it, mark it "unverified". The ledger and status file
say what's running, never what needs the user.

## Make things move

- Stalled card: read its log (`hermes kanban log <id>`), then `kanban_comment` with the next step,
  `reclaim` a dead claim, or unblock it if the blocker is gone. Fix it yourself when it is small.
- For a completion sweep ("get all sessions green"), inspect each remaining deliverable and why it stopped,
  then resolve authorized asks and resume concrete work. If a recipient only relays the instruction back,
  direct it to produce the missing artifact in its own session; a coordination reply is not execution.
  Reuse existing cards; preserve physical, privacy and spend gates.
- For David's soak-only waits, run the existing check immediately rather than wait out an observation window.
  Record his duration waiver on the card, report the actual window, and close on checked evidence—not a
  fabricated full-soak pass. Repair failing checks without another soak-only delay. Quota resets, unfinished
  implementation and third-party locks are not soaks; never shorten real data-retention periods to test expiry.
- Before undoing a model migration, recover the original user instructions from the tool that performed it:
  Hermes `state.db` does not contain Claude Code's `~/.claude/projects/*/*.jsonl` user messages. Read the
  timestamped user rows and distinguish existing-card migration from later new-card defaults; a commit message
  or an incomplete search cannot establish that prior pins should be restored.
- Verify execution after dispatch: `cross_session.send` can return `delivered=true, status=streaming`
  while agent initialization subsequently fails. Read back the exact message, then require a new tool
  operation or substantive conclusion; correlate a silent chat's runtime ID with `logs/errors.log`.
  For authorized session-only model recovery, use the owning Desktop route's `config.set` RPC
  (`key=model`, concrete model plus `--provider`), never global/profile edits. If it reports
  `Exact pinned route cannot fail over`, inspect durable routing provenance: a failed pre-build
  switch may retain the old allocation. Preserve independent-review identity gates; do not edit
  SQLite or claim the switch worked without a successful RPC readback and actual execution.
- Finish in the originating session: a conclusion stating what was done, what was verified and
  why the work is finished, plus its done outcome. For obsolete work, state why it is no longer
  relevant instead of claiming implementation success. Archiving is not a substitute for this conclusion.
- Keep genuine unfinished work open. Only irreducible human actions become blocked cards with
  `human_request`; delegated judgment does not supply credentials, physical evidence or extra spend.
- Verify completion with `hermes sessions outcome <id> --refresh --json`, then read it back; without
  `--refresh` the command only reads stored state. Live card links can restore `waiting` after a done declaration. Trace remote links to the
  owner's `kanban notify-list <task> --json` and the card's named canonical origin. Remove only a demonstrably
  obsolete observer via supported `notify-unsubscribe`, sync owner and consumer, then re-read the derived
  outcome; preserve canonical ownership and unmet acceptance. If a child-context mutation fence refuses the
  cleanup and no corresponding tool exists, hand the exact operation and evidence to the parent; never route
  around the fence or edit the subscription database.

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
