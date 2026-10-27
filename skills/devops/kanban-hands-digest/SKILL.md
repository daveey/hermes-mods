---
name: kanban-hands-digest
description: "Use when asked about Kanban blockers or human steps. Clear blockers and explain stalled cards in a scoped digest."
version: 1.0.0
author: daveey
license: MIT
metadata:
  hermes:
    tags: [kanban, digest, blocked, human-in-the-loop, cron, auto-unblock]
    related_skills: [kanban-worker, kanban-orchestrator]
---

# Kanban hands-on digest

Clear agent-solvable blockers; consolidate verified human-only actions into **one ordered checklist**.
For an all-session fleet audit, use the inventory-and-session-join procedure in
`references/clearing-blockers-yourself.md` before claiming complete coverage.

## Scope: this session by default

"What's blocked?" means **this chat's cards** (the ones it created or is subscribed to, i.e. its task strip),
not every board. **"What do you need me to do?" is wider.** He is reading the Desktop "For you" panel, which
lists every human-blocked or gave-up card on the gateway, not only the thread you were just discussing. Before
you say "nothing needed", sweep the board for `blocked` (`needs_input`/`capability`) and `gave_up` cards
(`hermes kanban list` plus `kanban_show` on each) and clear them. He calls an answer of "nothing" given while
that panel still shows asks a lie.

The card set for the session-scoped question: Find them from this session's own `kanban_create` results /
task strip, then `kanban_show` each and report the blocked ones. Also report two states that wait silently:
- `triage` with a human `block_kind` (`needs_input`/`capability`): block-loop detection moved a repeatedly
  re-blocked card there. It still waits on the user, and the latest `block_loop_detected` event holds the ask.
  Filtering on `status='blocked'` alone (as the digest script does) misses these.
- `scheduled` cards whose wake time has passed, or that have no wake time at all (no `wake_at` on the
  latest `continued`/`scheduled` event, or a wake cron that no longer exists): they sit unrun
  indefinitely. Re-queue with `hermes kanban unblock <id>` (it accepts scheduled cards). A `needs_input`
  card parked in scheduled is an ask of the user; report it.
Before presenting an unblock request:
1. Read the latest task events; delayed crash notices do not establish current state. Distinguish worker failure from an explicit human ask—never substitute the task body's opening sentence.
2. Report cause → current retry/paused state → recovery owner. For a genuine ask, name the action, device, reason and observable completion condition. Otherwise say no user action is identified; do not repeatedly requeue a refusal or switch providers to evade it.
3. `hermes kanban stats` "Waiting on you: N" and diag `waiting_behind_user` are not a list of asks: they include quota-wall holds (`blocker_auth`) and asks that went stale. Check each premise live (`gh workflow list`, device state, `kanban_show` events) before it goes on his list; clear or archive the false ones yourself.
4. When the user calls the messaging useless, fix its producer and rendered surface, not just the explanation. Keep operational pauses visible as agent issues without Done buttons. Test failure → nonhuman pause → genuine human ask, including rendered buttons/counts; helper tests alone miss misleading controls.

## This user: clear it yourself, never hand him cards

Treat a complaint about recurring stalls as recovery work, not a sequence of audit/status replies. When correction is authorized, pursue both existing-card recovery and tested prevention with the existing owners; label each separately until deployed behavior is verified.
1. Sweep blocked cards and clear agent-operable causes through supported transitions (recipe: `references/clearing-blockers-yourself.md`). Keep true approvals and safety holds; verify a new claim or completed acceptance, not merely `ready`.
Applies to every board-status reply, including "why are only N running": what waits on him is written as concrete actions grouped by where he does them (reply here / phone / Mac / when on site), each with the device and what done looks like. No card ids, no "see t_x", no table of cards for him to open.
2. Bring genuine decisions to him INLINE as a `clarify` form. Use one question per decision in plain words, recommended option first, and batch the low-stakes defaults into one "accept all recommended" question. Never answer with card ids or links, or with "answer in a comment on t_x". Then record his answers on the cards yourself (comment the decision on the card and on every card waiting on it, then `hermes kanban complete` the decision card).
   Before you relay a human ask, re-test its premise; block reasons go stale. "Reconnect Desktop / the relay has no connected client" is disproved by a successful `cross_session read` of the target session. Check "needs a credential/access" against what other profiles on the host already hold, and against local cloud SSO profiles (`aws sts get-caller-identity --profile <p>` may already be admin in the named account). Check that a PR the ask names is still open, and that a requested restart hasn't already happened. If the premise is false, comment the evidence and the working route, then unblock.
   An old scope-hold comment ("STOP remediation until told") on a card counts as superseded only with evidence, such as sibling cards in the same program now landing under the same authority. Say in your reply that you treated it as lifted and offer to re-hold.
3. Do sign-in/OAuth blockers yourself through the hub's agent browser before asking him for anything (same reference). Only a step that truly needs his hand or device (a password not in the vault, Touch ID, a physical action) goes to him, and only after you have tried the self-service route.
4. A "walk me through it" request still means doing everything automatable yourself; the walk-through covers only what remains.

When the user's step is done, re-queue the card yourself (`kanban_unblock`, or the CLI for triage) after a
comment saying what changed. Don't wait for the card to notice. Go global (the digest script below, all
boards) only when the user says so ("globally", "all boards", "everything") or the session has no cards — then
say it's a global view.

## Global digest

```
python3 <skill_dir>/scripts/hands_digest.py --config <your config.json> --run-checks --no-unblock
```

Paste the output as-is (plain text). `--run-checks` runs the auto-checks so already-done steps move to the
"Already done" section; `--no-unblock` is right inside a Kanban worker or delegate child, where the
`hermes kanban unblock` CLI is refused anyway. Drop `--no-unblock` in a normal session to let passing
checks unblock their cards. Other flags: `--json`, `--max-chars N` (shrinks item text to fit a chat post),
`--checks-only` (silent unless it unblocked something; for a frequent cron), `--dry-run`, `--home`.

## What it reads

Every board (`$HERMES_HOME/kanban.db` + `kanban/boards/*/kanban.db`, `_archived` skipped), read-only.
A card is included when `status='blocked'` and its latest `blocked` event has kind `needs_input` or
`capability`, or it was created blocked (`initial_status`) — then the body's structured keys or its
`Blocked until:` line are the reason. `transient`/dependency blocks are not human work.

## Block-reason format workers should write

```
ACTION: <one imperative line the human can do, with the device>
DEVICE: <group name from config, e.g. iPhone>
UNBLOCKS-WHEN: <observable condition>
CHECK: <check name> [args]          (optional; must exist in config `checks`)
DEADLINE: 2026-10-18T19:55-07:00    (optional; hard deadline, e.g. an invite expiry)
```

Keys may be on separate lines or run together in one line; `ACTION:` may repeat. Free-text reasons still
work through heuristics (imperative sentences kept, shipping/status chatter dropped, `expires <date>` and
`before/after HH:MM PT` picked up), but the structured form gives a clean one-liner.

## Config (JSON; keep the personal one outside this repo)

`references/config-example.json` shows every key:
- `groups`: ordered `{name, keywords, weight}`; a card goes to the group with the highest weighted keyword
  count. Display order = list order, after the TIME-SENSITIVE block (deadline within
  `deadline_horizon_hours`).
- `shared_steps`: dedupe one step shared by several cards. `sentence` regex picks the clause (only the
  matching clause is removed from each card's own action), `value` regex extracts a number, `agg`
  (`max`/`min`) combines them, `label`/`check` format with `{max}`.
- `rules`: `{match, group, check, action, unblocks_when}` applied by regex to the reason.
- `cards`: per-card overrides by id.
- `checks`: `{name: {cmd, unblock, timeout}}`. `cmd` is a shell command formatted with the check's args
  (`{0}`, `{args}`). Exit 0 = satisfied, 1 = not yet, else error; the first stdout line is shown. With
  `unblock: true`, a passing check bound to a card runs `hermes kanban [--board B] unblock --reason
  "auto-check ... passed: <line>" <id>`. Checks bound to a shared step only mark the step done.

## Scheduling (Hermes cron, no-agent)

Two no-agent jobs pointing at a small wrapper in `~/.hermes/scripts/` that passes your config:
a morning digest (`0 8 * * *`, `--run-checks --max-chars 3400`) and a frequent auto-unblock pass
(`every 15m`, `--checks-only`, empty stdout = silent). Prove delivery once with `hermes cron run <id>`.

## Tests

```
uv run --with pytest python -m pytest -q -p no:cacheprovider skills/devops/kanban-hands-digest/tests
```

Covers board discovery/filtering, structured + free-text parsing, shared-step dedupe, deadline/urgency
ordering, grouping, check runner + auto-unblock through a fake `hermes` binary (incl. `--board`),
`--max-chars` shrinking, and the CLI's `--json` / silent `--checks-only`.

## Pitfalls

- A `mode=ro` sqlite open can fail transiently while a writer truncates the WAL; the reader retries,
  then falls back to `query_only`.
- Checks that read a remote DB must not `.backup` a big live database per call (tens of seconds); open
  it `-readonly` with a busy timeout.
- Inside a Kanban worker the `hermes kanban` CLI refuses mutations, so auto-unblock only works from cron or
  a normal session; run on-demand with `--no-unblock` there.
