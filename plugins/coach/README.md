# coach

One nudge bus for Hermes, so coaching panels never pile up. It has two channels.

## Agent channel (invisible to you)

The plugin watches the agent's own tool calls. When it spots a known anti-pattern, it attaches a one-line hint to the
tool result the model reads next: a `coach_hint` key on JSON results, otherwise a `[coach] …` line. A hint still
pending at the end of a turn rides the next turn's `pre_llm_call` context. Each rule fires once per key per session.

| Rule | Trigger | Hint |
|---|---|---|
| `repeat_failure` | the same (whitespace-normalized) command or code fails 3× | stop retrying, reproduce, form one hypothesis, test it (systematic-debugging) |
| `poll_loop` | `sleep` inside a `while/until/for` loop, or ≥3 sleep/poll calls in the last 10 | `terminal(background=true, notify=true)` or a Kanban card, then end the turn |
| `no_checkpoint` | 40 tool calls in one user turn | write a state summary before more tool calls |
| `live_checkout` | `write_file`/`patch` under the running Hermes checkout, or `git commit/pull/reset/…`, `sed -i`, `npm run pack` there | use `hermes-core-wt new/ship` |

The live checkout is the tree that `hermes_cli` is imported from. Override it with `COACH_LIVE_CHECKOUT`.

**Live-checkout guard (`pre_tool_call`, blocks before running).** A terminal command that would move the live
checkout's HEAD (`git commit/pull/reset/checkout/switch/merge/rebase/am/cherry-pick/revert`, attributed per
command through `git -C` or the last `cd`), repack the running app (`npm run pack`, `hermes desktop --build`) or run
`hermes update` is refused, and the message points at `hermes-core-wt`. Why: each HEAD move makes every backend
report code skew, Desktop restarts, and in-flight turns die in every chat. On 2026-10-13 the MacBook Desktop quit
about 10 times this way. `git checkout -- <file>` (discarding live edits), read-only git, worktrees and scratch
clones pass. A backtest over 37.6k past Hermes terminal calls blocks 43, and every sampled one was a real live-tree
HEAD move or repack. Escape hatch for a change the user explicitly asked for: start the command with
`COACH_ALLOW_LIVE=1`. Blocks are logged as `agent_block` in `log.jsonl`.

### Reply check (`pre_verify`, before the turn ends)

When the agent stops with its final message, `reply_check.py` looks at it once per turn. A regex prefilter
finds candidates. The aux task `coach_reply` then judges them, given the user's ask, the turn's tool log and a
private environment profile (`<HERMES_HOME>/coach/reply_profile.md`: which credential stores, hosts and browsers
this user's agents can reach). A hit keeps the turn going with a nudge the user never sees.

| Rule | Fires when | Nudge |
|---|---|---|
| `handoff` | the reply hands the user a step the agent could do itself (log in, click, run, paste...) | try it first with shell/ssh/browser/vault/token-broker; hand off only what needs hands or judgment, and say why |
| `confirm` | the reply ends with "Want me to…?" about a reversible, in-scope step | proceed, verify, report; ask only before irreversible or external actions |
| `unverified` (off by default) | "done/fixed" with no check on the user's real path | verify on the user's path or say what's unchecked |

Measured on 360 real replies against hindsight labels (the labeler also saw the user's next message). At
threshold 0.7: `handoff` precision 0.91, recall 0.33; `confirm` precision 1.0 on 4 fires, recall 0.11. Held-out
samples are small (n=120), so treat these numbers as early. `unverified` showed no usable signal.
Settings: `COACH_REPLY_RULES` (comma list or `none`) and `COACH_REPLY_THRESHOLD`. Needs core
`agent.pre_verify_every_turn: true` (fork commit `4c93d9c632`), because `pre_verify` otherwise fires only after
file edits. Nudges are logged as `reply_nudge` in `log.jsonl`.

### Fix loops across turns (`pre_llm_call` + `post_llm_call`)

`post_llm_call` remembers each turn's final answer and tool log. On the next user message, `fix_loop.py`
prefilters it (still / again / I don't see it / a bare screenshot ...), and the aux task `coach_fixloop` judges
whether the user is reporting that the agent's claimed fix failed. Confirmed failures count within 8 user turns:

- 1st: hint to reproduce on the user's actual path, root-cause, and verify it the way the user will see it.
- 2nd and later: hint to stop patching, list what was tried plus hypotheses, check the distinguishing evidence,
  consider a read-only second opinion or a clean-restart brief.

The user-channel `still_broken` panel now counts these judge-confirmed failures (2 = Anthropic's "after two
failed corrections") in place of 3 regex hits. Set `COACH_FIXLOOP=0` to go back to the regex.
Backtest on 313 real (reply → user) pairs against hindsight labels (the labeler also saw the next turn), at
0.8: precision 0.84, recall 0.35. The old regex scored 0.47 / 0.15. Held-out fired only 3 times, so its 0.33
says little; most of the disagreements were cases the hindsight labeler excused, like "it turned out to be the
old GIF".

### Lessons from corrections (`lessons.py`)

Every real user message is judged by the aux task `coach_lessons`. Bare acks ("go", "do it", "ok"), slash
commands and image-only sends are skipped. The judge runs in a background thread, so the turn never waits, and its
hint arrives with the next tool result. A keyword gate caught only 51% of the 138 corrections in 700 mined
exchanges. The skip list misses none of them and judges ~85% of messages. When a message is a correction, the judge
extracts a standing rule (`rule`, `slug`, `scope`), or nothing if it was a one-off. It sees the known rules, so a
restated rule reuses the existing slug instead of starting a new candidate. Each rule lands in
`<HERMES_HOME>/coach/lessons.json` as a **candidate**. When its slug recurs in a 2nd session it is **promoted**.
A rejected slug stays rejected. A `saved` rule (already in memory) only produces a reminder. The agent gets the rule as a hint right away. Once a rule is promoted, the hint
also tells it to save the rule with the memory tool unless memory already has it. `COACH_LESSONS=0` turns this off.

## User channel (Desktop panel above the composer)

Each panel has one primary button and Dismiss. No menus.

| Rule | Trigger | Button |
|---|---|---|
| `still_broken` | 3rd user turn like "still not working / same error / didn't work" | **Restart clean**: a new session whose first message is a brief (Goal / Context / Tried so far / Repro / Done when) + an `@session` link |
| `long_session` | over 250 messages (counted across compressions) | **Spin out**: runs the spin-out plugin's planner, and its panel takes over. **Compact** (secondary): runs `/compress` in the session |
| `screenshot_midturn` | an image with fewer than 3 words, sent while a turn runs | **Add a line**: prefills "About the screenshot: " |
| `move_to_board` | the 10th subagent in the session, or a job over 10 min in chat (a tool call that ran ≥10 min, or a background job still being polled 10 min after it started) | **Move to board**: a handoff card (brief + `@session`) on the active board, made with spin-out's `kanban_cards` |
| `hermes_bug` | the same Hermes failure signature (an exception in a Hermes tool, a traceback from Hermes code, or a Hermes-infra tool error such as `cross_session`) in 2+ sessions within 7 days | **File it once**: one card on the `hermes` board (idempotent per signature, never offered again) |

### Guardrails (one gate, server side)

- At most 1 nudge per session per 15 user turns.
- A dismissal mutes that rule for the session.
- 3 dismissals of a rule within 7 days demote it to digest-only. It is then logged as `digest` and not shown.
- Everything goes to `<HERMES_HOME>/coach/log.jsonl`: every shown nudge, every choice (`accepted` / `dismissed` / `expired`), every digest-only suppression and every agent hint. `GET /api/plugins/coach/stats?days=7` reports precision per rule as accepted ÷ (shown − expired), which is KPI 4.

### Feature curriculum (tips)

`curriculum.yaml` lists features to learn: each entry has a live `trigger`, a one-line `tip`, an optional `try`
drill, the `commands` it names, and an `adopted` test. A private overlay at `<HERMES_HOME>/coach/curriculum.yaml`
adds entries or overrides them by `id` (`enabled: false` drops one). The file header documents both mini-languages.

| Tip | Trigger | Retires when (14 days) |
|---|---|---|
| `project_bot` | 6th session in the same repo in 14 days | 3 interactive sessions in a non-default profile (a Bot) |
| `kanban_max_runtime` | a job over 10 min in chat | `kanban_create` with `max_runtime_seconds` in 3 sessions |
| `todo_checklist` | a turn with 3+ list items, and no todo use yet | todo used in 3 sessions |
| `cron_whenever` | "every day / whenever / each time" | a cron job created in 3 sessions |
| `worktree_parallel` | a 2nd live session in the same repo | 2 sessions in a linked git worktree (sibling `../repo-x` or `.worktrees/`), or 2 worktree cards |
| `session_mention` | "as we discussed / in the other session" | `@session:` typed in 3 sessions |
| `kanban_l1_boards` | "follow-ups / next steps / backlog" | 5 cards created from your own chats |
| `kanban_l2_fanout` | 5 subagents, or 3 `delegate_task` calls | 3 cards with `parents`, or one `kanban swarm` |
| `kanban_l3_review` | "deploy / ship / you said it was done" | 3 `request_review` / `request_changes` |
| `kanban_l3_goal_mode` | 2nd "still not working" | 3 `goal_mode` cards |
| `kanban_l4_triage` | a message from a phone platform, or "at some point / no rush" | 3 triage cards, or 2 `specify` / `decompose` runs |
| `kanban_l4_specialists` | 3 `kanban_create` calls in the session | 3 cards with a non-default assignee |

How a tip shows: tips rank below every bus rule, pass the same gate (rule name `tip:<id>`), and appear as one panel
with **Try it** (pre-fills the drill in the composer) or **Got it**. Spacing lives in `coach/curriculum_state.json`:
after a show, a tip waits 20 h before it can show again anywhere. An accept waits 3 days, and each dismissal doubles
the wait (2, 4, 8 … days, capped at 30). At most 2 tips show per day. A text trigger counts for 2 user turns.
`COACH_TIPS=0` turns tips off.

Retiring: the hermes-coach miner (`coach_mine.py --write-adoption`, a daily no-agent cron on the coach Bot)
evaluates every `adopted` test over the digests from all hosts and writes `reports/adoption.json` into the data repo.
Adoption is sticky. The plugin reads `~/code/hermes-coach-data/reports/adoption.json` (override with
`COACH_DATA_DIR` or `COACH_ADOPTION`), and an adopted tip never shows again. The Mac's coach feed copies the private
overlay to `curriculum/<host>.yaml` in the data repo, so the Bot checks those entries too. Those adopted tests read
the digest's `features` (content-free counts from tool-call args, digest v4), `tools` and `signals`.

### prompt-coach, topic-shift and spin-out share the gate

Decision: they **share the rate limiter but are not folded in**. Their panels offer a real choice (New session /
Fork / Send here; Run new / Replace / Keep), and squeezing that into a one-button nudge would lose it. Each one calls
`window.__coach.claim(stored, rule)` before showing and `window.__coach.report(stored, rule, event)` afterwards.
Both calls fail open when coach isn't loaded.

- Only one bus panel is visible per session at a time.
- Their dismissals mute them for the session, and the weekly demotion applies to them too.
- Their shows use up coach's 15-turn budget. They aren't blocked by that budget, because they respond to a message you just sent.
- spin-out: only its automatic panel goes through the gate. ⇧⌘F always shows.

## Files

- `__init__.py`: the hooks (`pre_llm_call`, `post_tool_call`, `transform_tool_result`).
- `agent_rules.py`: the agent-channel rules, plus the signals the user channel needs (subagents, long jobs, bug signatures).
- `core.py`: the file store under `<HERMES_HOME>/coach/` (`sessions/<id>.json`, `bugs.jsonl`, `log.jsonl`), the gate, the user-rule evaluation, the briefs (spin-out's transcript reader + aux task `coach`) and stats.
- `curriculum.py` + `curriculum.yaml`: the tips (loading/overlay, live triggers, spacing, adoption read).
- `dashboard/plugin_api.py`: `/state`, `/offer`, `/event`, `/restart`, `/board`, `/file-bug`, `/stats`.
- `desktop-plugins/coach/plugin.js`: the panel. It checks after each finished turn, every 60 s while a turn runs, and on screenshot sends.

The brief model is set with `auxiliary.coach.provider` / `.model` (default: the auto aux model).

## Tests

```bash
cd plugins/coach
uv run --with pytest --with fastapi --with httpx python -m pytest -q -p no:cacheprovider tests
PATH=~/.nvm/versions/node/v20.20.2/bin:$PATH bash tests/desktop/run.sh   # desktop flow, mocked SDK + real spin-out
# miner adoption over real data (Hermes venv has ruamel.yaml):
~/.hermes/hermes-agent/venv/bin/python ../../skills/productivity/hermes-coach/scripts/coach_mine.py --data ~/code/hermes-coach-data --top 0 --write-adoption
```
