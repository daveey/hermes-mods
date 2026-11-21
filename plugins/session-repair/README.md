# session-repair

[daveey 2026-11-20] "a scheduled job that scans sessions for weird or stuck statuses — like tasks waiting for workers
if a worker is available, unexplained waits, etc. I run into a lot of these and keep having to fix them; a
repair-session would help. It can then schedule fix cards as well." And: "make sure there is a concrete skill for
identifying broken sessions, so that when I find examples they go in there."

Three parts:

| part | what |
|---|---|
| `scan.py` | Read-only scanner. Every chat of this gateway with an open linked card (`tasks.session_id` or an unreleased `task_chat_links` row), walked with the session-panel feed (`Feed.compute` over its `CoreReaders`, loaded by path), so cards on each chain are judged too; plus every recent chat in `state.db` for undelivered notices. One rule per pattern of the [`broken-sessions`](../../skills/devops/broken-sessions/SKILL.md) skill, same ids. Prints `{findings, errors, chats, cards, duration_s}`. |
| `run.py` | The cron tick: scan, 24 h dedupe per fingerprint, caps, then log (shadow) or start one turn in the **Session repair** chat (live). |
| `dashboard/plugin_api.py` | `GET /api/plugins/session-repair/findings` (read-only): the tick's last scan, `?chat=<id>` to filter, `?fresh=1` to scan now without storing anything. For a future session-panel pip. |

## Patterns (v1)

| id | detects | automatic fix (live) |
|---|---|---|
| BS-ready-worker-free | ready > 10 min, no dispatcher skip in 15 min, and a free slot: host `running < max_in_progress`, `pool_could_run`, or a fitting active/unheld/undrained machine with `max_card_runs - active_card_runs` above the cards ahead (slots allotted oldest waiter first); or a ready card still holding a claim | `hermes kanban dispatch --max 1` (host slot only) |
| BS-overdue-wake | scheduled: clock wake > 30 min late, wake probe > 30 min past `next_at`, or no wake at all for > 30 min | `hermes kanban unblock` (clock; probe after a manual check) |
| BS-unexplained-wait | blocked/triage: invalid `human_request`, blocked with no kind and no reason > 10 min, or agent-owned wait > 2 h (never a valid ask) | none: fix card |
| BS-parent-done | todo / dependency-blocked whose parents all finished > 10 min ago; a park awaiting a card finished > 10 min ago | `promote` / `unblock` |
| BS-stuck-run | running: claim or lease expired > 5 min, no claim/lease/run, or no event (heartbeats count) > 60 min | none: fix card (never reclaims) |
| BS-review-no-reviewer | review with no running run > 60 min after the newest review event | `dispatch --max 1` when review dispatch is on |
| BS-broken-chain | a parent or awaited card no board/replica/remote-parent row has; a dependency cycle | none: fix card |
| BS-notice-undelivered | last message a `kanban_notice` whose close-out ended `error` / `busy`, deferred > 2 h past due, claimed > 2 h with no verdict, or never claimed > 2 h (not human-blocked); a `tui` subscription with an event undelivered > 60 min | one close-out turn (`hermes chat --resume <id> --oneshot`) |

A finding: `{id, variant, card, board, chat, title, summary, evidence, fix: {kind: auto|card|human, action,
commands, why}, fingerprint}`; fingerprint = `<id>:<board>/<card>` (`<id>:chat/<session>` for a chat-level one).

Only this gateway's live board files are judged. A replica card (another gateway's) is skipped: its owner's scanner
judges it. Read-only: every handle is a session-panel `CoreReaders` connection (`mode=rw` + `PRAGMA query_only=ON`),
so a core helper that tried to write raises instead (`tests/test_readonly.py`).

## Modes and the repair chat

`SESSION_REPAIR` env, else `$HERMES_HOME/session-repair/config.json` `{"mode": ...}`, default **shadow**:

- `off`: nothing.
- `shadow`: findings not seen in 24 h go to `actions-shadow.jsonl` as `would: fix|file`; no turn, no write.
- `live`: those findings (under the caps) go to ONE turn in the chat titled **Session repair**:
  `hermes chat -c "Session repair" --create-if-missing --oneshot -Q --source cli --query-file last-prompt.md`
  (resolves the existing chat by title, creates it the first time). The prompt is `prompt.md` + the findings JSON; it
  names the skill (a resumed chat refuses `-s`, so the turn loads it with `skill_view`). The turn applies the `auto`
  fixes with the Kanban CLI, files one fix card per `card` finding (`kanban_create`, idempotency key
  `session-repair:<fingerprint>`, `self_work={version:1, repository:'hermes-mods', area:'session-repair',
  kind:'sweep'}` when the schema has it, linked to the repair chat because it is created there), and a blocked card
  with a `human_request` for a `human` one. It never deletes or archives. After the first turn the chat is pinned
  (`hermes sessions pin`: CLI-created chats are otherwise hidden from the Desktop sidebar).
- Caps: card-needing findings 5 per run and 20 per 24 h (ledger `ledger-<mode>.jsonl`), safe fixes 20 per run;
  the rest stay unseen and come back next tick. A failed turn marks nothing seen (retried next tick, exit 1).

State (`$HERMES_HOME/session-repair/`): `config.json`, `seen-<mode>.json`, `ledger-<mode>.jsonl`,
`actions-shadow.jsonl`, `last.json` (the route's source), `last-prompt.md`, `last-turn.txt`, `chat.json`, `run.lock`.

## Scheduling: Hermes cron, no-agent script

The tick is a Hermes cron **no-agent** job every 15 min (`hermes cron create --no-agent --script`, core
`cron/AGENTS.md`: "`no_agent=True` makes the script the whole job"; empty stdout = silent). Cron has no fixed-session
option for agent jobs (each run gets its own session; `--continuity` only injects the previous output), and a
`wakeAgent` gated agent job would also run in a new session each time, so the script starts the agent turn itself
with `hermes chat -c "Session repair" --create-if-missing`, the supported way to append to one titled chat
(`hermes_cli/main.py` `_resolve_continue_arg`). The scan runs only when the tick runs; the turn only when something
is new.

## Install (hub)

Not installed by this change. After the commit is on `main` and hermes-sync has linked
`~/.hermes/plugins/session-repair` on the hub:

```sh
ssh ubuntu@hermes-hub
cd ~/.hermes/plugins/session-repair
~/.hermes/hermes-agent/.venv/bin/python install.py            # shows the plan
~/.hermes/hermes-agent/.venv/bin/python install.py --apply    # wrapper script + config (shadow) + cron job
hermes cron list | grep -A8 session-repair
tail -f ~/.hermes/session-repair/actions-shadow.jsonl         # what live would do
~/.hermes/hermes-agent/.venv/bin/python install.py --apply --mode live   # when shadow looks right
hermes plugins enable session-repair                           # only for the /findings route
```

`install.py --apply` writes `~/.hermes/scripts/session_repair_tick.py` (runs `plugins/session-repair/run.py`), writes
`config.json` (`shadow` unless `--mode`), and runs `hermes cron create "every 15m" --name session-repair --no-agent
--script session_repair_tick.py --deliver local --catch-up off` unless a job of that name exists. Run it again: it
does nothing. To stop: `hermes cron pause <id>` or `--mode off`.

## Tests

```sh
uv run --no-project --python 3.14 --with pytest --with pyyaml --with fastapi --with httpx --with ruamel.yaml --with psutil python -m pytest -q -p no:cacheprovider plugins/session-repair/tests
```

- `tests/test_patterns.py`: every pattern, positive and negative, on a board built with core's `kanban_db` in a temp
  `HERMES_HOME` (`tests/fixtures/patterns.py`, one builder per state); the machine lane's slot allotment (registry and
  queue view stubbed); a chain card of another chat judged through the linked card; a healthy board with no finding.
- `tests/test_readonly.py`: a scan that finds every rule leaves the board and `state.db` byte-identical; every
  connection refuses writes; a rule that tries to write is a counted error, not a write.
- `tests/test_run.py`: modes, 24 h dedupe, caps (5/run, 20/day, a new day), shadow logs and starts no turn, live
  starts exactly one `hermes chat -c "Session repair"` turn (fake runner) with the filled prompt, a failed turn is
  retried, shadow/live dedupe apart, pinning once, the lock.
- `tests/test_routes.py`: `/findings` through FastAPI (last scan, chat filter, `fresh` scans and stores nothing,
  GET only).
- `tests/test_install.py`: the plan, `--apply`, idempotence.

Untested: a real `hermes chat` turn and what the agent does with the prompt; the cron job firing on the hub; the
scanner against a WAL board on Linux under load (it was dry-run once on the hub's live data, read-only).
