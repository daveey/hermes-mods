# Labeling sessions: the anti-pattern rubric

Each queued session gets 0–3 findings. Label what the evidence shows (user turns, signals, tool and error counts,
last reply). Never infer from the title alone. When a session looks fine, write `{"session_id": ..., "pattern": "none"}`
so it leaves the queue.

## Finding format (one JSON object per line, `findings/<YYYY-MM-DD>.jsonl`)

```json
{"session_id": "20261010_144150_b8ec02", "host": "macbook", "profile": "default", "title": "...",
 "pattern": "repeat_fix_loop", "side": "user|agent|system", "severity": 1,
 "evidence": "<short quote or count from the digest>", "waste_usd_est": 40,
 "fix": "<one concrete change>", "fix_kind": "habit|skill|config|plugin|core|curriculum",
 "feature": "<Hermes feature that would have helped, if any>"}
```

Severity: 1 = minor friction, 2 = clearly cost time or money, 3 = most of the session was wasted.
`waste_usd_est` is a rough share of the session cost (incl. children) attributable to the pattern; say 0 if unsure.

## Patterns

User side
- `vague_opener`: the first turn has no outcome or done-criteria, and later turns supply them piecemeal.
- `kitchen_sink_session`: unrelated tasks pile into one session (topic changes, many `title`-unrelated asks) → spin-out / new session.
- `session_bloat`: >300 msgs or the same task re-scoped many times. Fix: restart clean with a brief, or `/goal`.
- `repeat_fix_loop`: ≥3 "still broken" turns with no new information. Fix: give a repro and a check the agent can run, use `/goal`,
  or restart with the distilled state.
- `screenshot_only_report`: a bug reported only by screenshot, with no text on what is expected or wrong.
- `re_explaining_context`: re-typing context that lives in another session or doc → `@session`, `@file`, a skill.
- `manual_repetition`: the same check or chore asked for across sessions → cron / Bot routine / monitor.
- `should_be_kanban`: long or parallel work orchestrated in chat: ≥15 subagents (`children.n`), status pings
  ("how goes", "resume") while long jobs run, multi-phase sessions, or "check back later". Fix: board + cards (fan-out,
  synthesizer, reviewer, `max_runtime_seconds`). Name the cards it should have been.
- `permission_ping_pong`: user keeps answering "do it" / "yes" / "go". Usually agent-side (asks needlessly); label it agent-side then.

Agent side
- `asks_needlessly`: confirmation menus or "want me to…?" when the request was clear (the user has said he dislikes this).
- `guess_not_reproduce`: changes code without reproducing or observing the failure, so the user reports it again.
- `unverified_done`: claims success without running the check, then the user says it doesn't work.
- `tool_error_loop`: the same tool or command failing repeatedly (see `tool_errors`) without changing approach.
- `polling_waste`: sleep/poll loops instead of background + notify, or subagents waiting on each other.
- `context_blowup`: huge outputs, re-reading the same files, or heavy compression (`synthetic_summary` signals).
- `skill_gap`: a skill existed (or should have) and was missing or ignored. Name the skill and the missing step.

System side (Hermes itself)
- `hermes_bug`: stalls, crashes, interrupted turns, UI bugs the user had to stop and fix (`stuck` signals,
  `synthetic_*`, hermes-meta sessions). Count it once per root cause across sessions, since the same bug recurring is the signal.
- `provider_trouble`: rate limits, outages, auth churn eating the session.

## Proposals (in the daily report)

Rank by `waste_usd_est` summed across sessions, then by recurrence. Each proposal names exactly one change: a skill
patch (skill + step), a config key, a coach rule (trigger → nudge), a curriculum tip, or a plugin/core fix. Each needs
evidence: ≥2 sessions, or 1 session with severity 3. Never propose something listed in the user's rejected list
(e.g. Claude account swapping).
