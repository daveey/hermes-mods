# prompt-coach

After you send a task request in Hermes Desktop, Prompt Coach checks whether a more ambitious
framing would get a much better result. If it would, a panel above the composer shows a one-line
critique and an editable rewrite. The lenses come from Felix Rieseberg's
[How to be more ambitious](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models):

- **Altitude**: ask for the outcome, not the step ("keep it crash-free", not "fix this crash").
- **Depth**: work the way a top expert would. Raw data, real instruments, a stretch target, an outside judge.
- **Range**: restate nouns as verbs and borrow methods from other fields.

Buttons:

- **Run in new session** (primary): opens a fresh session with the rewrite and an `@session` link back.
  Your original turn keeps running, so you can compare the two.
- **Replace**: stops the turn, `/undo`s your message and sends the rewrite in this session.
- **Keep mine**, or **don't coach this session**. The palette command *Prompt Coach: toggle* turns it off everywhere.

It never blocks or alters a send. The check runs after the message goes out, and any error stays silent.

## How it works

- `desktop-plugins/prompt-coach/plugin.js` adds pass-through composer middleware that observes each send
  and calls `POST /api/plugins/prompt-coach/check`. It renders the panel in `COMPOSER_AREAS.top`.
- `core.py`, stage 1 (aux task `prompt_coach`, a cheap model): is this a fresh task request with room to
  grow? Slash commands, messages under 8 words, sends queued behind a running turn, and rewrites we launched
  are skipped before any model call.
- Stage 2 (aux task `prompt_coach_rewrite`, a strong model): picks a lens and writes the critique, the
  rewrite and a confidence score. The panel shows only when confidence is at least 0.8.
- Every screened request and every choice is appended to `<HERMES_HOME>/prompt-coach/log.jsonl`.
  `digest.py [days]` summarizes the log (`GET /stats?days=7` does the same).

## Setup

```bash
hermes plugins enable prompt-coach
# Optional model choice (otherwise both stages use your main model):
hermes config set auxiliary.prompt_coach.provider anthropic
hermes config set auxiliary.prompt_coach.model claude-haiku-5-5
hermes config set auxiliary.prompt_coach_rewrite.provider anthropic
hermes config set auxiliary.prompt_coach_rewrite.model claude-sonnet-5-5
```

Reload the Desktop window (⇧⌘R) to load the panel. The backend route mounts on first request.

Optional weekly digest: a cron job whose script runs `digest.py 7`, with the `ambitious-requests`
skill, that writes a short coaching note from the JSON.

## Calibration

The thresholds were set on a backtest of 236 real requests. The cheap model's `gain` score sits near 0.3
for anything with upside, so it serves only as a loose pre-filter (gate 0.3). The strong model's confidence
does the real gating. At 0.8 the coach fired on 8.5% of requests, and every one was a useful rewrite. The
0.7 band added generic "root-cause this screenshot" boilerplate. Latency is about 1s when skipped and
about 6s when it rewrites. The panel appears after the send, so the send itself never waits.

## Tests

```bash
cd plugins/prompt-coach
uv run --with pytest --with pyyaml --with fastapi --with httpx python -m pytest -q -p no:cacheprovider tests
PATH=~/.nvm/versions/node/v24.13.0/bin:$PATH tests/desktop/run.sh   # desktop flow, mocked SDK, Node >= 22
```
