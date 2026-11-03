# skill-outcomes

`.usage.json` counts skill loads, not whether the skill helped. This plugin writes one line per skill use to
`<HERMES_HOME>/logs/skill-outcomes.jsonl`:

```json
{"ts": 1793680000.0, "host": "hub", "session": "20261103_...", "skill": "five-whys",
 "checks_first_pass": true, "fix_rounds": 0, "user_correction": false,
 "checks": "5/5", "has_done_means": true, "correction_source": "next_message", "turn_id": "..."}
```

## How each field is filled

| Field | Source |
|---|---|
| `skill`, `ts` | `post_tool_call` on a successful full `skill_view` (no `file_path`). One use per (session, turn, skill). |
| `host`, `session` | `socket.gethostname()` (short), the hook's `session_id`. |
| `checks_first_pass`, `fix_rounds`, `checks` | When the skill has a `## Done means` section, `transform_tool_result` appends one line to the `skill_view` result asking the agent to end its reply with `Done means (<skill>): <passed>/<total> first pass, <k> fix rounds`. `post_llm_call` parses it from the turn's final reply: `checks_first_pass` = passed == total. If the turn's first final reply lacks a loaded skill's tick-off, `pre_final_gate` holds it once and asks for a re-send with the line (`attempt` > 0 always delivers). No tick-off (or no Done means section) → `null`. `tickoff_mismatch` = the reported total differs from the skill's list (left out of the rollup). |
| `user_correction`, `correction_source`, `rules` | The record stays pending (`cache/skill-outcomes/pending.json`) until the session's next real user message (`pre_llm_call`; when the hook missed it, e.g. across a plugin reload, the flush reads it from `<home>/state.db`, where any `display_kind` other than `steer` marks a Hermes-written turn, and tags the line `reply_source: state_db`). Machine turns are skipped: Kanban done/blocked notices, delegation and compaction notices, relays, canonical decisions, runtime notes (this plugin's list plus coach's `self_heal.SYNTHETIC_RE` and `reply_check.NOTICE_RE`, loaded by path). 3 h later it is finalized (coach's backstop judge lags: measured p50 34 min, p90 2 h): `keyword` = coach's correction regex (`plugins/coach/lessons.py` `keyword_hit`, a built-in copy when coach is absent) matched the message; `coach` = coach logged a `selfheal_detect` (complaint/backstop) or `lesson` event for the session, after that message, whose evidence is in its text (no bare time window: a backstop on the turn's own opening message lands in the same window); otherwise `next_message` (false). No user message within 2 h of the turn (Kanban, cron) → false, `none`. A pending record an older version closed on a machine notice is reopened and, if no real message follows, finalized `null`, `reopened`. Lines carry `rules: 3`; the rollup leaves older lines out of the correction rate (rules 2 lines missed real replies while a reloaded gateway paired new hooks with a cached old core; hook failures now log once at WARNING). |

Due records are written by a sweep in the hooks (at most once a minute) and by `python3 outcomes.py flush
[--home H] [--all-profiles]`. `python3 outcomes.py rollup [--days 7]` prints the per-skill table (uses, tick-offs,
first-pass rate, mean fix rounds, correction rate) that the hermes-coach weekly KPI post includes.

## Tests

`tests/test_skill_outcomes.py`: one test per filling path (skill capture from `skill_view`, the transform's ask,
tick-off present / absent / no Done means section, correction by keyword, by coach log event, no next message
→ false after 2 h, synthetic user turns ignored), the flush timing, and the rollup.
