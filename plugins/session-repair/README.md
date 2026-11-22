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

| id | detects (variants) | automatic fix (live) |
|---|---|---|
| BS-ready-worker-free | ready with no dispatcher skip recorded in its ready episode (any age; a timed hold whose `until` passed does not count), and: `leftover_claim` (still holds a claim, > 10 min); `pool_could_run` (> 10 min); `free_slot` (core `kanban_dispatch_skip.unrecorded()`: >= 30 min, host `running < limit`); `free_worker` (> 10 min, a fitting active/unheld/undrained machine with `max_card_runs - active_card_runs` above the cards ahead; slots allotted oldest waiter first) | `hermes kanban dispatch --max 1` (`free_slot` only) |
| BS-overdue-wake | scheduled: `clock_overdue` (wake > 30 min late), `probe_overdue` (probe > 30 min past `next_at`), `no_wake` (no wake for > 30 min) | `hermes kanban unblock` (`clock_overdue` only; a probe park is a card: run the probe first) |
| BS-unexplained-wait | blocked/triage, never a valid ask: `invalid_human_request`; `no_reason` (no kind, no `reason`/`error`, >= 30 min); `agent_owned_stalled` (> 2 h; core `AGENT_BLOCK_KINDS`) | none: fix card |
| BS-parent-done | `parents_done`: todo / dependency-blocked whose parents (core `parent_states`) are all done/archived > 10 min, with none of core's holds (containment, spend decision, `recovery_held`); `awaited_done`: a park awaiting a card done/archived > 10 min | `unblock` (`awaited_done` only; `parents_done` is a promotion-defect card) |
| BS-stuck-run | running: `claim_expired` / `lease_expired` (> 5 min), `no_claim`, `quiet` (no event, heartbeats count, > 60 min) | none: fix card (never reclaims) |
| BS-review-no-reviewer | review with no running run > 60 min after the newest review event: `idle` (review dispatch on), `idle_manual_review` (off) | `dispatch --max 1` (`idle` only) |
| BS-broken-chain | `missing_parent`, `missing_awaited`, `cycle` | none: fix card |
| BS-notice-undelivered | last message a `kanban_notice` whose close-out ended `error` / `busy`, deferred > 2 h past due, claimed > 2 h with no verdict, or never claimed > 2 h (not human-blocked); `subscription_lag` (a `tui` subscription with an event undelivered > 60 min). Never the repair chat | one close-out turn (`hermes chat --resume <id> --oneshot`) |

A finding: `{id, variant, card, board, chat, title, summary, evidence, fix: {kind: auto|card, action, commands,
why}, area: hermes|card, fingerprint}`; fingerprint = `<id>:<board>/<card>` (`<id>:chat/<session>` for a chat-level
one). `area` says who a fix card is for: `hermes` (Hermes machinery misbehaved) or `card` (the card owner's work).

Never judged: the Session repair chat (its `chat.json` id and any chat titled "Session repair"), cards the repair
session created (idempotency key `session-repair:...`, cards its turns reported as `card`, and cards the self-work
intake created for a request they reported as `queued` or that carried a `session-repair:` key; a card only
commented on or fixed stays judged), worker-node copies (core `foreign_ids`). A `card` finding with an open fix card
for its fingerprint (key range `session-repair:<fp>:`, any day, any board, or a card its turns reported creating for
it, not finished) carries `fix.open_fix_card`; the turn comments there. An agent-owned stall that chat-closeout's
owner wake already handled for the same premise (`owner-wake.db`: `started`/`routed` in 24 h, or a live claim younger
than its `CLAIM_TTL_S`) is skipped. Chats over
`MAX_CHATS` (1000) are counted in `truncated_chats` and logged.

Only this gateway's live board files are judged. A replica card (another gateway's) is skipped: its owner's scanner
judges it. Read-only: every handle is a session-panel `CoreReaders` connection (`mode=rw` + `PRAGMA query_only=ON`),
so a core helper that tried to write raises instead (`tests/test_readonly.py`).

## Modes and the repair chat

`SESSION_REPAIR` env, else `$HERMES_HOME/session-repair/config.json` `{"mode": ...}`, default **shadow**:

- `off`: no scan or repair (still records a tick summary).
- `shadow`: findings not seen in 24 h go to `actions-shadow.jsonl` as `would: fix|file`; no turn, no write.
- `live`: those findings (under the caps) go to ONE turn in the chat titled **Session repair**:
  `hermes chat -c "Session repair" --create-if-missing --oneshot -Q --source cli --query-file last-prompt.md`
  (resolves the existing chat by title, creates it the first time; the argv is tested against core's own parser).
  The prompt is `prompt.md` + the findings JSON, each with an `idempotency_key` `session-repair:<fingerprint>:<UTC
  YYYYMMDD>`; it names the skill (a resumed chat refuses `-s`). The turn applies the `auto` fixes, files fix cards
  by the card policy (look for open work first and comment instead; name open work on the subsystem; `area: card` =
  product work, never `self_work`; `area: hermes` = Hermes is the exception: comment on the area's open card, a new
  card only on a dispatcher/credential/quota/money path or when it blocks the card, `self_work` only then and only
  when the schema exposes it; a queued acknowledgment is kept, never retried undeclared), never deletes or archives,
  and ends with a `session-repair-result` block giving each fingerprint's outcome. After the turn the chat is pinned
  and its id written to `chat.json`.
- Seen only when handled: the result block's `fixed`, `card`, `queued`, `commented`, `moved_on`, `false_positive`,
  or `deferred` with a `reason` (24 h). `over_budget` returns next tick with no attempt, only when the turn created its
  whole card budget; an unknown outcome, a reasonless `deferred`, an unearned `over_budget` or a finding left out of
  the block is a failed attempt. The block's `card` is only a card the turn created (or a queued request id);
  comments go in `commented_on`.
- Caps: at most 5 fix cards per run (the prompt's budget includes cards for safe fixes that did not take) and 20 per
  24 h, counted from the cards the turn reports (`ledger-<mode>.jsonl`); safe fixes 20 per run.
- Failures: a turn that exits non-zero or reports no result block counts one attempt for each finding it was given
  (retry after 15 min, doubling, at most 6 h) and backs off the next turn the same way; after 5 attempts a finding is
  given up for 24 h (ledger `gave_up`). The tick exits 1, so the cron job's failure target (`bot-chat` by default,
  never `local`) shows it.
- `ledger-*.jsonl` and `actions-shadow.jsonl` keep their newest 2000 lines.

State (`$HERMES_HOME/session-repair/`): `config.json`, `seen-<mode>.json`, `ledger-<mode>.jsonl`,
`actions-shadow.jsonl`, `attempts-<mode>.json`, `last.json` (the route's source), `last-prompt.md`, `last-turn.txt`,
`chat.json`, `run.lock`, `ticks.jsonl`, `ticks.jsonl.1`, `ticks.lock`.

Every tick appends an fsynced summary to `ticks.jsonl`, including silent/deduped scans, off/lock skips,
scanner exceptions and failed turns: `ts` (epoch seconds), `mode`, `chats`, `cards`, `findings` (count),
`errors`, `error_count`, `duration_s`, `scanned`, `skipped`, `handed`, and `build` (SHA-256 of scan/run sources).
A separate lock serializes journal appends. Whole segments rotate at 4 MiB, retaining one previous segment
(about 8 MiB bounded retention; normal 15-minute ticks retain far longer than 24 hours). An unwritable journal
fails the cron tick rather than claiming an auditable success. No cron output-history retention is needed.
For rollout, require a full 24-hour same-build shadow window with no errors, no unexplained gaps/skips, and
adjudicate every would-act row in `actions-shadow.jsonl` (including any still-deduped findings in `last.json`).
Do not infer clean shadow from a missing journal or from zero repair turns.

Unexplained-wait detection excludes canonical pending spend decisions in triage, including retained HOLDs:
these need an explicit owner decision, not a generic unblock. For other waits, an old generic park and the
panel's projected age cannot predate the latest run's admission/end boundary. This scanner-side guard avoids
reusing core projections from an earlier episode; it does not change the card, its budget or its block kind.

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
                                                               # (--failure-deliver <target> to change bot-chat)
hermes cron list | grep -A8 session-repair
tail -f ~/.hermes/session-repair/actions-shadow.jsonl         # what live would do
~/.hermes/hermes-agent/.venv/bin/python install.py --apply --mode live   # when shadow looks right
hermes plugins enable session-repair                           # only for the /findings route
```

`install.py --apply` writes `~/.hermes/scripts/session_repair_tick.py` (runs `plugins/session-repair/run.py`), writes
`config.json` (`shadow` unless `--mode`), and runs `hermes cron create "every 15m" --name session-repair --no-agent
--script session_repair_tick.py --deliver local --failure-deliver bot-chat --catch-up off` unless a job of that name
exists (`--failure-deliver local` is refused: failures must show). For `bot-chat[:profile]` the plan first checks,
with core's own helpers, that the profile has a canonical Bot Chat authority (`find_canonical_live_owner`) and
warning notifications on (`warning_notifications_enabled`, as cron's failure delivery does); otherwise it prints
`refuse:` and exits 2 without installing anything (the other steps print as `would ...`): pass another
`--failure-deliver`. An existing job whose `failure_deliver` differs is reconciled with `hermes cron edit <id>
--failure-deliver <target>` after the same check. Run it again: it
does nothing. To stop: `hermes cron pause <id>` or `--mode off`.

## Tests

```sh
uv run --no-project --python 3.14 --with pytest --with pyyaml --with fastapi --with httpx --with ruamel.yaml --with psutil python -m pytest -q -p no:cacheprovider plugins/session-repair/tests
```

- `tests/test_patterns.py`: every pattern and variant, positive and negative, on a board built with core's
  `kanban_db` in a temp `HERMES_HOME` (`tests/fixtures/patterns.py`): a skip recorded in the episode explains a wait
  however old, core's 30-min unrecorded rule, `pool_could_run`, probe-first `probe_overdue`, `no_reason` threshold and
  `error` reasons, core's wait constants, cancelled parents and core's holds (breaker, containment), worker copies
  skipped by every rule, `lease_expired` / `no_claim`, review dispatch off, `missing_awaited`, the repair chat and
  its own cards excluded (key, ledger ids, self-work intake receipts), open fix cards named on day two (prefix
  match, finished ones ignored), chat-closeout's handled stalls skipped, no `human` kind, `MAX_CHATS` truncation, the notice re-delivery command parsed by core's
  parser, the machine lane's slot allotment (registry and queue view stubbed), a chain card judged through the
  linked card, a healthy board with no finding.
- `tests/test_readonly.py`: a scan that finds every rule leaves the board and `state.db` byte-identical; every
  connection refuses writes; a rule that tries to write is a counted error, not a write.
- `tests/test_run.py`: modes, 24 h dedupe, caps (5/run, 20/day, cards for failed safe fixes counted, a new day),
  shadow logs and starts no turn, live starts one turn (fake `hermes`) and marks only what its result block handled,
  outcomes (`deferred` with a reason seen, `over_budget` next tick, unknown/reasonless/left out = attempts, `gave_up`
  rows), an open fix card reaching the prompt, exponential backoff and give-up after 5, no result block = failed, bounded
  logs, pinning, the lock, the turn argv parsed by core's own parser, and the prompt's card-policy text.
- `tests/test_routes.py`: `/findings` through FastAPI (last scan, chat filter, `fresh` scans and stores nothing,
  GET only).
- `tests/test_install.py`: the plan, `--apply`, idempotence, `--failure-deliver` (never `local`), the Bot Chat check
  (no owner, authority not ready, warnings suppressed: refused), the cron argv parsed by core's cron parser.

Untested: a real `hermes chat` turn and what the agent does with the prompt; the cron job firing on the hub; the
scanner against a WAL board on Linux under load (it was dry-run once on the hub's live data, read-only).
