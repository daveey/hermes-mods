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

## What belongs in the Inbox (and the noise breaker)

The Inbox is for **decisions only David can make**: spend, an irreversible action, a judgment call. A failure an
agent can fix (red CI, a broken job, a stuck card) goes to a **Kanban card** (assignee `default`), not here, and a
card never duplicates a Kanban card that is already doing the work. Routine status goes nowhere.

**Ask contract (every door).** `post()` is the one gate for the tool, `hermes inbox post`, REST and any replica
apply. A post must name a registered `poster` (`posters.yaml`; local extras in `<home>/inbox/posters.yaml`; the
family is the text before `·`, so `kanban · <title>` is `kanban`) and carry `ask={interaction, why_human, action}`
(`approval|credential|publish_as_david|physical|preference`), except cards whose type has `resolve` actions. Agent-owned
families (`kanban`, `coach`, `cron`) list the decision types they really post (no `*`), and their resolve-typed
titles still go through the status/defect wording check (`content_status`/`content_defect`; quoted third-party
text such as a reply draft's tweet is skipped; `self-heal` opts out with `defect_titles`, since `selfheal_reason`
gates it). Their non-resolve asks count only when derived from the card's `human_request`. Test
or status wording (`TEST`, `scratch`, `CTESTLIVE`, `9999-..` dates, `failed`/`expired`/`X DM sent`), an
unregistered or type-forbidden poster, a missing ask, and any write to the real inbox under pytest/`HERMES_TEST`
are refused. A refusal is never silent: it becomes a Kanban card (`inbox-refused:<family>:<key>`; repeats are
comments) and the poster gets `AgentOwnedPost`. An `ask_missing`/`ask_invalid` refusal names the retry shape (`ask={interaction, why_human, action}`) in the error and the Kanban copy, so the calling agent reposts with `ask` instead of leaving a real owner ask on the board. A `kanban` poster inside a worker whose card has a valid `human_request` gets `ask` filled from it (`user_terminal`/`physical` -> `physical`, `reply` -> `preference`, `external` -> `approval`). `@handle` markers are test junk too: `scratch` anywhere in a handle, or a standalone `test` segment (`@test`, `@acme_test2`); `@latest`, `@contest`, `@testarossa` pass. **The poster label is bound to the runtime.** Inside a Kanban worker (`HERMES_KANBAN_TASK`, or `HERMES_KANBAN_WORKER_TASK`, which is what a worker's shell children keep; any door) or a cron run (`HERMES_CRON_SESSION`), a declared `cli`/`desktop`/`oneshot` poster (the unrestricted `*` families) is re-labelled `kanban · <declared>` / `cron · <declared>` before admission, so the kanban/cron type lists, the self-attest rule and the defect wording check apply; the refusal Kanban card names both labels. The declared label stays for project routing. Restricted posters (social, slack, coach) keep their own bounded type lists. Without those markers nothing changes. `tests/test_poster_conformance.py` scans hermes-mods, daveey/hermes
and hermes-hub call sites (`HERMES_POSTER_ROOTS` overrides) and fails on a literal poster that is not registered.
Nested `.claude/worktrees/` and `.worktrees/` copies are excluded: they are not live producers.
Other `.claude` sources and unregistered posters in authoritative files remain checked
(`tests/test_poster_scan_worktrees.py`).

**Noise breaker v2** (`store.py`, `MUTE_AFTER` = 2, `MUTE_WINDOW_S` = 7 days, keyed by poster *family*, poster-less
posts counted): when a family's last 2 closed cards in the window were archived with no action, a new card is
refused with `PosterMuted` and one Kanban card `inbox-muted:<family>` is filed. Updates to an already-open card
with the same key still go through. After fixing the producer: `hermes inbox unmute '<family>' --fix-commit <sha>`
(a commit SHA or GitHub commit URL is required and recorded).

**Junk** (`hermes inbox junk <session_id>`, `POST /items/{id}/junk`, the trash button on a Desktop row): archives the
card, mutes its family + key prefix, and files a five-whys Kanban card (`inbox-junk:<family>:<prefix>`) with the
card's snapshot. The daily `inbox-junk-audit` cron (`junk_audit.py`) opens one Kanban card per poster family that
still shows junk.

**Junk guards** (bb-ce4c46ed94; each earlier fix blocked one sender by name and the next one got through):
- *Archive sticks:* when the newest card with a key was archived with no action, a re-post of that key is refused
  for `ARCHIVE_STICK_S` (14 days) and filed as one Kanban card (`inbox-archived-key:<key>`).
  `hermes inbox unarchive-key '<key>'` releases it for a genuinely new decision.
- *Every post has a sender:* a keyed post with no poster is attributed to its key family (`key · gh-actions-billing`);
  a post with neither poster nor key is refused (`NoPoster`). The agent tool falls back to `agent · <profile>`.
- *Per job, not per card:* `kanban · <card>`, `desktop · <chat>` and `oneshot · <x>` count as one sender each for the
  noise breaker (`poster_group`); `cron · <job>` is already per job. Key-family senders are never muted: those flows
  are one key per item and are covered by the archive rule.
- *No test/probe cards:* titles/keys that look like tests or probes (`TEST:`, `e2e routing probe`, `SCRATCH`,
  `safe to ignore`, keys like `x-post:TEST3`, `bsky-routing-probe:`) are refused (`JunkPost`), and so is any post
  from a pytest run (or `HERMES_INBOX_TEST=1`) into a real `~/.hermes` / `~/.bb-hermes` home.
- *Detection:* `junk_audit.py` (daily hub cron `inbox-junk-audit`) reads every gateway's Inbox through
  `hermes-hub inbox --all --json` and opens one Kanban card per day when junk still got through.
Tests: `tests/test_store.py` (streak, reset, sidebar archive, window, re-key), `tests/test_mute_paths.py` (CLI, tool, REST).

**Agent-fixable refusal** (`store.agent_fixable_reason`): before any card exists, a post from an agent-fixable
poster (`AGENT_FIXABLE_POSTERS`, today `kanban-dispatcher`) or whose Do-it prompt tells an agent to fix and unblock a
Kanban card is refused with `AgentFixablePost` (a `ValueError`, same CLI/tool/REST handling). Such state goes on the
Kanban board (comment or fix card). The noise breaker only fires after David archived 3 cards; this needs none.
Tests: `tests/test_mute_paths.py::test_agent_fixable_posts_refused_before_any_card_exists`.

## Social chats and cards go to the `pr` project

Desktop groups a session under the project owning its `cwd`. Social Inbox cards (workflow-owned `x-`/`bsky-`/`bluesky-`/`social-` types or keys, or titles requesting X/Twitter/Bluesky publishing, drafts, approvals, replies or DMs) get the owning host's `pr` project folder from its own `projects.db`. IDs and paths are never copied between gateways. Free-text coding tasks such as “Fix X reply API retry bug” and “Merge PR #123 for Twitter post scheduler” do not match; “Draft a Bluesky post about PR #123” does. Explicit `cwd` wins, including home; an inherited directory already inside `pr` is retained. Reposts never move sessions.

Ordinary unassigned Desktop/TUI chats use the same classifier on their first user turn, before model/tools run. The registered `pre_llm_call` hook finds that exact session in the owning backend, checks both runtime and stored workspace provenance, and uses core's workspace setter to update the live cwd, persisted row and session-info event. It does not select/focus a chat or invalidate the prompt cache. Explicit/pinned projects, persisted cwds (even home), resumed/branched chats, workers, CLI sessions and room plumbing are untouched. A missing project/directory or missing local runtime is a conservative no-op. No polling or historical ordinary-chat reassignment.

`hermes inbox backfill-social [--apply]` considers fallback-directory cards only with recorded non-explicit provenance AND an authoritative `ended_at`; explicit choices, legacy unknown provenance, unended sessions and archived cards are skipped (archive opt-in available). Writes recheck lifecycle/cwd atomically. No age/message-count liveness guess. Code: `routing.py`, `chat_routing.py`, `store.py`; regression tests: `tests/test_social_routing.py`, `tests/test_chat_routing.py` (registered hook, owner-profile lookup, coding negatives, overrides and worker exclusions).

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

## Conditional posting for pollers

Default posting behavior is unchanged. Pollers that must not revive a dismissed key or overwrite
user activity can opt in through the owner-side store or CLI:

```sh
hermes inbox post 'Draft options' --key draft:example --prompt 'Review options' --if-absent
hermes inbox outcome draft:example
hermes inbox post 'Refreshed options' --key draft:example --prompt 'Review options' --expected "$snapshot_json"
```

- `Inbox.post(..., if_absent=True)` creates only when **no sidecar row with that key exists**,
  including archived, accepted, resolved, queuing, and missing-session records.
- `Inbox.post(..., expected=snapshot)` refreshes only the unchanged, still-open newest card.
  Pass the JSON object returned by `outcome`, or a previous conditional result's `snapshot`.
  Required fields are `key`, `session_id`, and opaque `revision`; do not manufacture the token.
- Both modes require `key` and are mutually exclusive. Success returns
  `{applied: true, session_id, updated, snapshot}`. A failed condition returns
  `{applied: false, reason: "exists" | "changed", snapshot: <current outcome or null>}` with **no post**.
  CLI exit codes: **0** applied, **3** condition conflict, **1** invalid payload, **2** argparse errors.
  Invalid `--expected` JSON (including `null`) fails closed. Do not fall back to unconditional posting
  on a conflict or error; reassess the current snapshot and caller eligibility policy.
- Outcomes add `revision`, `item_state`, `updated_at`, `message_count`, and `last_message_id`.
  The revision hashes the complete sidecar row, authoritative session row and all stored message rows;
  same-count edits/deletes and messages from core writers invalidate it, as do lifecycle changes.
  It is a content snapshot token, not a monotonic event counter. Reading/metadata changes can also
  cause conservative conflicts. Queuing cards retain public `state: open` but cannot be refreshed.
- `ATTACH inbox.db` **before** `BEGIN IMMEDIATE` reserves both SQLite writers before checking the
  condition, and holds them through transcript and sidecar updates/commit. Core session writers need
  no plugin lock participation. Core transcript encoding, counters and lease guards are reused.
  Exceptions roll back both stores. This guarantees concurrency isolation, **not cross-file
  power-loss atomicity**: SQLite WAL cannot guarantee the latter for attached databases.
- The conditional path honors type validation, routing, mute refusal and the read-card cap.
  Unlike the default path, a conditional mute refusal does not queue a remediation Kanban card.
  Only the store and CLI expose conditions; the agent tool and REST posting surface remain unchanged.

Tests: `tests/test_conditional.py` uses real SessionDBs, subprocess CLI entry points, competing
create/refresh processes, independent state/sidecar writers, archive/accept/edit/message changes,
invalid conditions, snapshot readback and injected rollback. It invokes the plugin's registered
argparse/CLI implementation without loading unrelated plugins from a live profile.

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
- **⌥1…⌥9** (Alt+1…9 off macOS) on an open card run the bar's buttons in order; each button shows its key (`kbd`, `aria-keyshortcuts`) and Archive shows ⌘⇧A. A capture-phase window `keydown` listener matches `event.code` (⌥digit types ¡™£… on macOS) with no ⌘/⌃/⇧, so it works while typing in the composer and the character is not typed. Top-row digits only (not the numpad: Windows Alt+numpad codes). Passed through untouched when the open chat isn't a card or its bar isn't on screen, during IME composition, on key repeat, from an editable outside the chat surface, a dialog or a terminal, when core already claimed the chord (`defaultPrevented`), or when ⌥digit types a layout character other than the US ¡™£¢∞§¶•ª (German/French/Nordic ⌥5…⌥9 = `[ ] | { } @ ~` still type in the composer). Swallowed without running anything while the card's inline editor is open or its action is in flight. An `input` action's key opens its editor. A user keybind on ⌥digit in Settings → Keybinds would conflict on a card (the plugin uses `stopImmediatePropagation`, but core's listener may run first). Why ⌥: ⌘1–9 / ⌃1–9 / ⌘⌥1–9 are core's profile, session and tab slots; bare digits would need focus out of the composer. These keys don't appear in Settings → Keybinds (contributed keybinds can't fire from the composer with Alt chords).
- Archive / `resolve` / `task` on the open card step to the next message; on the last one to the session core's ⌘⇧A would open (`ctx.openNextSession`, fork core patch 0157); an older core opens a fresh draft instead of leaving you on the archived chat. If you moved to another chat while the action's request was in flight, nothing navigates. The row's hover Archive does the same when that row is the open chat.
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
