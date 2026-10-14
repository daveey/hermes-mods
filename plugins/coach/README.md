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

## User channel (Desktop panel above the composer)

Each panel has one primary button and Dismiss. No menus.

| Rule | Trigger | Button |
|---|---|---|
| `still_broken` | 3rd user turn like "still not working / same error / didn't work" | **Restart clean**: a new session whose first message is a brief (Goal / Context / Tried so far / Repro / Done when) + an `@session` link |
| `long_session` | over 250 messages (counted across compressions) | **Spin out**: runs the spin-out plugin's planner, and its panel takes over |
| `screenshot_midturn` | an image with fewer than 3 words, sent while a turn runs | **Add a line**: prefills "About the screenshot: " |
| `move_to_board` | the 10th subagent in the session, or a job over 10 min in chat (a tool call that ran ≥10 min, or a background job still being polled 10 min after it started) | **Move to board**: a handoff card (brief + `@session`) on the active board, made with spin-out's `kanban_cards` |
| `hermes_bug` | the same Hermes failure signature (an exception in a Hermes tool, a traceback from Hermes code, or a Hermes-infra tool error such as `cross_session`) in 2+ sessions within 7 days | **File it once**: one card on the `hermes` board (idempotent per signature, never offered again) |

### Guardrails (one gate, server side)

- At most 1 nudge per session per 15 user turns.
- A dismissal mutes that rule for the session.
- 3 dismissals of a rule within 7 days demote it to digest-only. It is then logged as `digest` and not shown.
- Everything goes to `<HERMES_HOME>/coach/log.jsonl`: every shown nudge, every choice (`accepted` / `dismissed` / `expired`), every digest-only suppression and every agent hint. `GET /api/plugins/coach/stats?days=7` reports precision per rule as accepted ÷ (shown − expired), which is KPI 4.

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
- `dashboard/plugin_api.py`: `/state`, `/offer`, `/event`, `/restart`, `/board`, `/file-bug`, `/stats`.
- `desktop-plugins/coach/plugin.js`: the panel. It checks after each finished turn, every 60 s while a turn runs, and on screenshot sends.

The brief model is set with `auxiliary.coach.provider` / `.model` (default: the auto aux model).

## Tests

```bash
cd plugins/coach
uv run --with pytest --with fastapi --with httpx python -m pytest -q -p no:cacheprovider tests
PATH=~/.nvm/versions/node/v20.20.2/bin:$PATH bash tests/desktop/run.sh   # desktop flow, mocked SDK + real spin-out
```
