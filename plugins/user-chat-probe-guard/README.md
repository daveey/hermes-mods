# user-chat-probe-guard

Agents never send test turns into a person's chat.

A fix worker proved its fix with
`hermes chat --resume <David's Desktop chat> --oneshot -q "... Reply exactly ROUTE_INSTALLED_23ef49"`. The probe and
the bare token became the newest turns of his real chat and buried the one question waiting for him (self-heal
agent-93a55211b1).

The `pre_tool_call` hook refuses a `terminal`, `process` or `execute_code` call (also inside `bash -c` payloads and
nested `tool_call` batches) that resumes a session (`--resume/-r <id>`, `--continue/-c <title>`) **with a query**
(`-q/--query`, `--query-file`, `-z/--oneshot`) when the session, looked up in this profile's `state.db`, is a
person's chat:

- source is user-facing (desktop, cli, tui or a messaging platform), and
- its first user message is not itself an agent probe (`Reply exactly <TOKEN>`), and
- it is not the caller's own session and not a probe copy (`_probe_copy_of`).

The refusal names the replacement:

    copy=$(hermes sessions branch <id>)          # hidden oneshot copy with the full history (core, [daveey])
    hermes chat --resume "$copy" --oneshot -q '<probe>'
    hermes sessions delete --yes "$copy"

Allowed: interactive resume (no query), worker/internal sessions (kanban, cron, oneshot, tool, ...), a Desktop
session an agent opened for its own live test, ids this host's store does not know (e.g. a remote host's chat
over ssh: the hub-side `probe_token_watch.py` in the coach plugin covers those after the fact).

Opt-out when the user asked for a turn in that very chat: prefix `HERMES_PROBE_REAL_CHAT_OK=1` (or set it in the
process env). `HERMES_USER_CHAT_PROBE_GUARD=off` disables the hook.

## Claude Code

`claude_hook.py` is a Bash `PreToolUse` adapter over the same classifier (exit 2 blocks):

    python3 ~/.hermes/plugins/user-chat-probe-guard/claude_hook.py --install

## Detection

`plugins/coach/probe_token_watch.py` (hub cron, hourly) reads every host's coach digests and files one Kanban card
per person's chat that still received a `Reply exactly <TOKEN>` probe or ends on a bare token.

## Tests

    python3 -m pytest -q -p no:cacheprovider plugins/user-chat-probe-guard/tests

`tests/test_user_chat_probe_guard.py`: the incident command and its variants (`-r`, `--resume=`, `--query-file`, `hermes -r X -z`,
global `-r` before `chat`, env/timeout prefixes, id suffix, `bash -lc`, `--continue <title>`) are refused from
terminal, execute_code (string and argv-list forms) and nested `tool_call`; probe copies, oneshot/kanban sessions,
agent-opened test sessions, empty sessions, the caller's own session, interactive resume, unknown ids, quoted or
echoed commands and the `sessions branch`/`delete` route are allowed; opt-out and off switch; a missing store never
breaks a call. The Claude adapter is run as a subprocess with hook JSON (block and allow); its `--install` path is the same code as backend-restart-guard's and is not separately tested here.
