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
| `checks_first_pass`, `fix_rounds`, `checks` | When the skill has a `## Done means` section, `transform_tool_result` appends one line to the `skill_view` result asking the agent to end its reply with `Done means (<skill>): <passed>/<total> first pass, <k> fix rounds`. `post_llm_call` parses it from the turn's final reply: `checks_first_pass` = passed == total. No tick-off (or no Done means section) → `null`. |
| `user_correction`, `correction_source` | The record stays pending (`cache/skill-outcomes/pending.json`) until the session's next real user message (`pre_llm_call`; Kanban notices and other synthetic turns are skipped). 15 min later it is finalized: `keyword` = coach's correction regex (`plugins/coach/lessons.py` `keyword_hit`, a built-in copy when coach is absent) matched the message; `coach` = coach logged a `selfheal_detect` (complaint/backstop) or `lesson` event for the session quoting that message or within 15 min of it; otherwise `next_message` (false). No user message within 2 h of the turn (Kanban, cron) → false, `none`. |

Due records are written by a sweep in the hooks (at most once a minute) and by `python3 outcomes.py flush
[--home H] [--all-profiles]`. `python3 outcomes.py rollup [--days 7]` prints the per-skill table (uses, tick-offs,
first-pass rate, mean fix rounds, correction rate) that the hermes-coach weekly KPI post includes.

## Tests

`tests/test_skill_outcomes.py`: one test per filling path (skill capture from `skill_view`, the transform's ask,
tick-off present / absent / no Done means section, correction by keyword, by coach log event, no next message
→ false after 2 h, synthetic user turns ignored), the flush timing, and the rollup.
