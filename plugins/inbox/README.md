# inbox

A Hermes Inbox: Hermes posts **proposals of work** you can read, run with one click, reply to, or archive. It shows as an **Inbox** section in the Desktop left pane, above Blocked.

Each message is a real session. It has the normal transcript, can be resumed and lives on its gateway. It is created hidden, so it stays out of Recents. Its first message (role `assistant`) is the proposal: what Hermes found, the evidence, and the exact instruction **Do it** will send.

| State | How it gets there | Where it shows |
|---|---|---|
| open, unread | `inbox_post` / `hermes inbox post` / `POST /items` | Inbox section, bold row + accent dot |
| open, read | you open it (any door that focuses the chat) | Inbox section, plain row |
| accepted | **Do it** (sends `accept_prompt`), or you type any reply | un-hidden → a normal chat in Recents |
| archived | Archive button (row hover / composer bar), `hermes inbox archive`, or the normal session archive | archived view (restorable) |

Re-posting with the same `key` updates the open message (new assistant note, title, prompt; marked unread again) instead of adding a duplicate. At most 20 open messages are kept. Past that, the oldest read ones are archived.

## Posting

- **Agent tool** `inbox_post(title, body, accept_prompt, key?, cwd?)`. The poster label (`cron · <job title>`, `kanban · …`) and cwd come from the calling session. The tool is deferred, so call it via `tool_call` (name `inbox_post`).
- **CLI**: `hermes inbox post "<title>" --body "<md>" (or '-' for stdin) --prompt "<instruction>" [--key K] [--poster P] [--cwd D]`, plus `hermes inbox list` and `hermes inbox archive <session_id>`.
- **REST** (`/api/plugins/inbox`): `GET /items`, `POST /items`, `GET /items/{sid}`, `POST /items/{sid}/accept`, `POST /items/{sid}/archive`.

## Desktop half

`desktop-plugins/inbox/plugin.js`:
- Registers a `SIDEBAR_SECTIONS_AREA` section (order 5, ahead of Kanban's Blocked at 10).
- Reads `/items` on every gateway (`ctx.rest(…, {connectionId})`) every 30 s while visible and after each action.
- Opens rows on their own gateway (owner route).
- Marks a message read through core's `PATCH /api/sessions/{id} {unread:false}` when its chat is focused.
- Adds a composer bar (**Do it** / **Archive**) when the focused chat is an open message.

The row's hover Archive button and bold unread title use the `actions`/`strong` props of `SidebarSectionRow` (fork core patch, see `core/sidebar-row-actions.md`). On an older core the row ignores them and still works: archive it from the composer bar.

## Data

The sidecar `<HERMES_HOME>/inbox/inbox.db` (`items`: session_id, key, title, poster, accept_prompt, created/updated, state) records which sessions are inbox messages and holds their metadata. Visibility is derived from the session row, so archiving or un-hiding the session another way is respected.

## Tests

```bash
SP=<runtime site-packages: lsof -p <hermes serve pid> | grep -o '/Users/.*/site-packages'>
PYTHONPATH=~/.hermes/hermes-agent:$SP <venv with pytest on Hermes Python 3.14>/bin/python -m pytest -q -p no:cacheprovider plugins/inbox/tests
/opt/homebrew/bin/node --test desktop-plugins/inbox/test/plugin.test.mjs
```
