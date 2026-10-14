# spin-out

Finds the follow-up work left at the end of a Hermes Desktop session and offers it in a panel above the
composer. For each follow-up you can:

- **click it** to launch it as a new session with a self-contained brief and an `@session` link back,
- **All (n)** to launch every remaining one,
- **Kanban (n)** to put the remaining ones on the active Kanban board, one card each. Cards are assigned to the
  session's profile, linked to the parent session, and work in its directory. A second click returns the same cards
  (idempotency key), so there are no duplicates.

## When it appears

- **Automatically, when a turn finishes** in the focused session and its last reply mentions follow-up work
  ("next steps", "still open", "not done yet", "want me to…"). A regex cue filter runs first, so most turns cost
  no model call. An unchanged reply reuses the cached plan, and a dismissed panel doesn't come back for the same reply.
  Palette: *Spin out: toggle automatic follow-up panel*.
  The automatic panel goes through the [coach](../coach/) gate (`window.__coach.claim/report`): it waits while another
  coaching panel is up, and a dismissal mutes it for the session.
- **On demand** with ⇧⌘F (always plans). On first load the built-in session search moves to ⌥⌘F.

## How it works

- `desktop-plugins/spin-out/plugin.js` watches `busyBySession` for the focused runtime flipping busy → idle,
  then calls `POST /api/plugins/spin-out/plan {auto: true}`. It also renders the banner.
- `core.py`: reads the transcript (read-only `state.db`) and asks the aux model (task `spin_out`) to split the
  follow-ups into independent, self-contained child prompts. `kanban_cards()` creates the cards through
  `hermes_cli.kanban_db.create_task`.
- `GET /ping` reports `api: 2`. Against an older backend (the plugin updated, but the backend not restarted yet), the
  desktop half skips the auto check and hides the Kanban button. Restart the backend (or the app) after updating.

## Tests

```bash
cd plugins/spin-out
uv run --with pytest --with fastapi --with httpx python -m pytest -q -p no:cacheprovider tests
PATH=~/.nvm/versions/node/v20.20.2/bin:$PATH bash tests/desktop/run.sh   # desktop flow, mocked SDK
```
