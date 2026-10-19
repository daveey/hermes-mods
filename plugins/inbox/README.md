# inbox

A Hermes Inbox: Hermes posts **proposals of work** you can read, run with one click, reply to, or archive. It shows as an **Inbox** section in the Desktop left pane, above Blocked.

Each message is a real session. It has the normal transcript, can be resumed and lives on its gateway. It is created hidden, so it stays out of Recents. Its first message (role `assistant`) is the proposal: what Hermes found, the evidence, and the exact instruction **Do it** will send.

| State | How it gets there | Where it shows |
|---|---|---|
| open, unread | `inbox_post` / `hermes inbox post` / `POST /items` | Inbox section, bold row + accent dot |
| open, read | you open it (any door that focuses the chat) | Inbox section, plain row |
| accepted | **Do it** (sends `accept_prompt`), or you type any reply | un-hidden → a normal chat in Recents |
| archived | Archive button (row hover / composer bar), `hermes inbox archive`, or the normal session archive | archived view (restorable) |

The card ends with the full **Proposed:** prompt, or, when the poster passes `prompt_label`, one line **Do it:** <label> (Do it still sends the whole prompt).

Re-posting with the same `key` updates the open message (new assistant note, title, prompt; marked unread again) instead of adding a duplicate. Once you replied to it (accepted, not archived), a re-post with that key lands in that chat as an unread update instead of opening a second card; after Archive the next post is a new card. At most 20 open messages are kept. Past that, the oldest read ones are archived.

## Posting

- **Agent tool** `inbox_post(title, body, accept_prompt, key?, cwd?, prompt_label?)`. The poster label (`cron · <job title>`, `kanban · …`) and cwd come from the calling session. The tool is deferred, so call it via `tool_call` (name `inbox_post`).
- **CLI**: `hermes inbox post "<title>" --body "<md>" (or '-' for stdin) --prompt "<instruction>" [--key K] [--poster P] [--cwd D] [--prompt-label L]`, plus `hermes inbox list` and `hermes inbox archive <session_id>`.
- **REST** (`/api/plugins/inbox`): `GET /items`, `POST /items`, `GET /items/{sid}`, `POST /items/{sid}/accept`, `POST /items/{sid}/archive`.

## Desktop half

`desktop-plugins/inbox/plugin.js`:
- Registers a `SIDEBAR_SECTIONS_AREA` section (order 5, ahead of Kanban's Blocked at 10).
- Reads `/items` on every gateway (`ctx.rest(…, {connectionId})`) every 30 s while visible and after each action.
- Opens rows on their own gateway (owner route).
- Marks a message read through core's `PATCH /api/sessions/{id} {unread:false}` when its chat is focused.
- Adds a composer bar (**Do it** / **Archive**) when the focused chat is an open message.
- **⌘⇧A** on a message archives it and opens the next message (the one below, else the top); on the last one it steps to the next session like core's ⌘⇧A. Uses `ctx.registerArchiveHotkeyHandler` (fork core patch 0147, `core/plugin-archive-hotkey.md`); on an older core ⌘⇧A archives through core.

The row's hover Archive button and bold unread title use the `actions`/`strong` props of `SidebarSectionRow` (fork core patch, see `core/sidebar-row-actions.md`). On an older core the row ignores them and still works: archive it from the composer bar.

## Profiles

Messages posted from a named profile (`<root>/profiles/<p>`) live in that profile's own sidecar and
`state.db`. The gateway's root dashboard is the only one Desktop asks, so `GET /items` also lists every
profile that has an inbox and tags each item with `profile`; accept / archive / get take `?profile=<p>`,
and the Desktop half opens such items with an owner route for that profile.

## Data

The sidecar `<HERMES_HOME>/inbox/inbox.db` (`items`: session_id, key, title, poster, accept_prompt, created/updated, state) records which sessions are inbox messages and holds their metadata. Visibility is derived from the session row, so archiving or un-hiding the session another way is respected.

## Tests

```bash
PYTHONPATH=~/.hermes/hermes-agent uv run --python 3.11 --with pytest --with ruamel.yaml --with pyyaml \
  --with python-dotenv --with fastapi --with httpx python -m pytest -q -p no:cacheprovider plugins/inbox/tests
/opt/homebrew/bin/node --test desktop-plugins/inbox/test/plugin.test.mjs
```
