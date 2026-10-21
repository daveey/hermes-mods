# inbox

A Hermes Inbox: Hermes posts **proposals of work** you can read, run with one click, reply to, or archive. It shows as an **Inbox** section in the Desktop left pane, above Blocked.

Each message is a real session. It has the normal transcript, can be resumed and lives on its gateway. It is created hidden, so it stays out of Recents. Its first message (role `assistant`) is the proposal: what Hermes found, the evidence, and the exact instruction **Do it** will send.

| State | How it gets there | Where it shows |
|---|---|---|
| open, unread | `inbox_post` / `hermes inbox post` / `POST /items` | Inbox section, bold row + accent dot |
| open, read | you open it (any door that focuses the chat) | Inbox section, plain row |
| accepted | **Do it** (sends `accept_prompt`), or you type any reply | un-hidden → a normal chat in Recents |
| resolved | a typed card's `resolve` action (Approve, Skip, Edit…) | archived view; decision readable via `outcome(key)` |
| archived | Archive button (row hover / composer bar), `hermes inbox archive`, or the normal session archive | archived view (restorable) |

The card ends with the full **Proposed:** prompt, or, when the poster passes `prompt_label`, one line **Do it:** <label> (Do it still sends the whole prompt).

Re-posting with the same `key` updates the open message (new assistant note, title, prompt; marked unread again) instead of adding a duplicate. Once you replied to it (accepted, not archived), a re-post with that key lands in that chat as an unread update instead of opening a second card; after Archive the next post is a new card. At most 20 open messages are kept. Past that, the oldest read ones are archived.

## Card types

A plain card has **Do it** + **Archive**. A *typed* card has its type's own buttons, and the poster reads the decision back later by key, so "archive" never doubles as "approve".

A type is a YAML file declaring fields and actions:

```yaml
name: x-post
label: X post
fields:
  id:   {required: true}
  text: {required: true, preview: true, max: 280}
  slot: {}
actions:
  - {id: approve, label: Approve, icon: check, style: primary, kind: resolve, row: true}
  - {id: edit,    label: Edit,    icon: edit,  kind: resolve, input: {field: text, multiline: true, max: 280}}
  - {id: skip,    label: Skip,    icon: close, kind: resolve}
  - id: rewrite
    label: Rewrite…
    kind: prompt
    input: {placeholder: What to change}
    prompt: "Rewrite X draft {{id}}: {{input}}. Update QUEUE.md and re-post key x-post:{{id}}."
```

| Action kind | What pressing it does |
|---|---|
| `resolve` | records `outcome=<action id>` and `value=<input>` (if the action has `input`; `input.field` also writes the value into that field), closes the card (state `resolved`, session archived). No agent turn. |
| `task` | `resolve` + queues background work: creates a Kanban card whose body is the rendered `prompt` (same templating and caps as `prompt`) through the core API (`hermes_cli.kanban_db.create_task`, the one `kanban_create` uses), then records the outcome and closes the card. Options: `assignee` (default `default`), `board` (optional; must exist), `title` template (default `<type label>: <action label> — <card title>`), `skills` list. The card id is stored on the item (`outcome_task`) and returned by `act`, `outcome(key)` (`task`), REST and CLI. Atomic: if the Kanban card can't be created the action fails (REST 500 with the message, CLI exit 1) and the inbox card stays open with no decision recorded. While the card is being created the item is in state `queuing` (readers, `outcome(key)` and the sidebar still see an open card with no decision); it becomes `resolved` once the Kanban card exists, and a claim stuck in `queuing` for over 5 minutes (a crashed claimant) can be retried. Retries reuse the same Kanban card (`idempotency_key` = `inbox:<session>:<action>`). The kanban home is shared by all profiles, so a card acted on with `?profile=` lands on the same board; `assignee` picks the profile that runs it. Desktop: behaves like `resolve` (card closes, next opens) plus a "Queued <task id>" toast. |
| `prompt` | renders `prompt` (`{{field}}`, `{{input}}`, `{{key}}`, `{{title}}`, `{{accept_prompt}}`; one plain substitution pass, values are never re-expanded) and runs it in the card's chat, like Do it. |
| Archive | always present (the id `archive` is reserved): dismissed, no decision, outcome `archived`. |

Action options: `input` (`field`, `multiline`, `max`, `placeholder`, `required` default true) opens an inline editor in the composer bar (char counter, Esc cancels, ⌘Enter saves); `confirm` (true or a message); `style` (`primary` first / `secondary` / `danger`); `icon` (a codicon name); `row: true` also shows the button on the sidebar row's hover (actions without `input` only). Field options: `required`, `max` (enforced at post time and on edit), `preview` (shown as a quoted block with a char count on the card), `label`. Validation is strict: unique action ids, known kinds only (`prompt`, `resolve`, `task`), prompt and task actions need a prompt that uses only declared fields, `assignee`/`board`/each skill match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` (and only `task` takes assignee/board/title/skills; `assignee` and `board` are also lowercase-only, no dots, because Kanban's profile and board names are), unknown keys are rejected, `row` is for `resolve`/`task` actions without input. YAML is safe-loaded as YAML 1.2 (PyYAML with only `true`/`false` as booleans, else ruamel.yaml, else JSON), so `id: no` stays a string; anchors/aliases and specs over 64 KB / 2000 nodes are rejected. A prompt action's button tooltip shows its template.

**Sources** (first wins): ad-hoc local `<HERMES_HOME>/inbox/types/<name>.yaml` (written by `inbox_types define` / `PUT /types/{name}`), then `<skill>/inbox-types/<name>.yaml` in every skills dir (`~/.hermes/skills` + `skills.external_dirs`), then the built-in `default` (`do_it`: prompt `{{accept_prompt}}`). Type names match `^[a-z0-9][a-z0-9_-]{0,63}$`. `define` refuses `default` and names a skill already owns (edit the skill's file instead). Ship a type with the skill that posts it.

**Snapshot**: the resolved type is copied onto the card at post time, so any gateway renders it without the registry and later edits to the type don't break open cards. Re-posting the same key refreshes the open card's type snapshot and fields. A type with `resolve` actions is a decision card: once acted on, a re-post with its key opens a new card instead of appending to the old chat.

Acting is atomic across processes (the sidecar row moves only from `open`); `act` on a card that is no longer open returns 409 over REST.

**Outcome**: `outcome(key)` returns the newest card with that key as `{state, outcome, value, fields, at, session_id, type, task}` (`task` = the Kanban card a `task` action queued), `state` ∈ open / accepted / resolved / archived (derived from the session too, so a sidebar archive reads as archived). Posters act only on an explicit resolve outcome.

Sidecar rows from before card types have no `type` and behave exactly as before (the `default` type).

## Posting

| Door | Commands |
|---|---|
| Agent tools (deferred: call via `tool_call`) | `inbox_post(title, body, accept_prompt?, key?, cwd?, prompt_label?, type?, fields?)`; `inbox_outcome(key)`; `inbox_types(action=list\|get\|define, name?, spec?)`. The poster label (`cron · <job title>`, `kanban · …`) and cwd come from the calling session. |
| CLI | `hermes inbox post "<title>" --body "<md>" (or '-' for stdin) [--prompt "<instruction>"] [--key K] [--poster P] [--cwd D] [--prompt-label L] [--type T] [--field k=v ...]`; `hermes inbox list`; `hermes inbox archive <sid>`; `hermes inbox act <sid> <action> [--input TEXT\|-]`; `hermes inbox outcome <key>`; `hermes inbox types [name]` |
| REST `/api/plugins/inbox` (`api: 3`) | `GET /items` (each with `type, type_label, fields, field_specs, actions`), `POST /items` (`type`, `fields`), `GET /items/{sid}`, `POST /items/{sid}/act {action, input?}`, `POST /items/{sid}/accept` (= act on the first prompt action; 409 on a card with resolve actions, so an older Desktop's Do it can't run it), `POST /items/{sid}/archive`, `GET /outcome?key=`, `GET /types`, `GET\|PUT /types/{name}`. Item routes and `/outcome`, `/types` take `?profile=`. |

`accept_prompt` is required only when the type has a prompt action that uses `{{accept_prompt}}` (the default type does).

## Desktop half

`desktop-plugins/inbox/plugin.js`:
- Registers a `SIDEBAR_SECTIONS_AREA` section (order 5, ahead of Kanban's Blocked at 10).
- Reads `/items` on every gateway (`ctx.rest(…, {connectionId})`) every 30 s while visible and after each action.
- Opens rows on their own gateway (owner route).
- Marks a message read through core's `PATCH /api/sessions/{id} {unread:false}` when its chat is focused.
- Adds a composer bar when the focused chat is an open message: the card type's actions (primary first, Archive last), with an inline editor for `input` actions and a confirm prompt for `confirm` ones. `prompt` actions submit the returned prompt into the chat; `resolve` and `task` actions close the card and open the next message (`task` also toasts "Queued <id>"). Items from a gateway without card types (`api` < 3, no `actions`) keep **Do it** / **Archive**.
- `row: true` actions appear on the row's hover next to Archive.
- **⌘⇧A** on a message archives it and opens the next message (the one below, else the top); on the last one it steps to the next session like core's ⌘⇧A. Uses `ctx.registerArchiveHotkeyHandler` (fork core patch 0147, `core/plugin-archive-hotkey.md`); on an older core ⌘⇧A archives through core.

The row's hover Archive button and bold unread title use the `actions`/`strong` props of `SidebarSectionRow` (fork core patch, see `core/sidebar-row-actions.md`). On an older core the row ignores them and still works: archive it from the composer bar.

## Profiles

Messages posted from a named profile (`<root>/profiles/<p>`) live in that profile's own sidecar and
`state.db`. The gateway's root dashboard is the only one Desktop asks, so `GET /items` also lists every
profile that has an inbox and tags each item with `profile`; accept / archive / get take `?profile=<p>`,
and the Desktop half opens such items with an owner route for that profile.

## Data

The sidecar `<HERMES_HOME>/inbox/inbox.db` (`items`: session_id, key, title, poster, accept_prompt, created/updated, state, plus `type, fields_json, actions_json, outcome, outcome_value, resolved_at, outcome_task`, added by an idempotent migration on open) records which sessions are inbox messages and holds their metadata. Visibility is derived from the session row, so archiving or un-hiding the session another way is respected.

## Tests

```bash
PYTHONPATH=~/.hermes/hermes-agent uv run --python 3.11 --with pytest --with ruamel.yaml --with pyyaml \
  --with python-dotenv --with fastapi --with httpx python -m pytest -q -p no:cacheprovider plugins/inbox/tests
/opt/homebrew/bin/node --test --test-reporter=tap desktop-plugins/inbox/test/plugin.test.mjs
```
