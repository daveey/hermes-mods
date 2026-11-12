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

## Restart-proof backends on a production home

A restart proof started `HERMES_DESKTOP=1 hermes serve --isolated` on the hub with the real `~/.hermes` home
(self-heal gateway-9aaf151a15). `--isolated` isolates the server endpoint, not the stored chats: the backend's
startup-resume sweep adopted David's real interrupted chat, and the proof's planned `kill -TERM` of that PID
interrupted his turn.

The same hook refuses an agent launch of `hermes serve`/`hermes dashboard` (also `python -m hermes_cli.main`,
`.../hermes_cli/main.py`) in the restart-proof shape (`--isolated` or `HERMES_DESKTOP=1`) whose `HERMES_HOME` is a
production home: not set inline (it inherits the live home), `~/.hermes`, `$HOME/.hermes`, `$HERMES_HOME`,
`.../.hermes/profiles/<name>`, or the caller's own home. It looks through `bash -c`, `ssh <host> '...'` and
`hub-admin '...'` (a remote launch counts only an inline `HERMES_HOME`), execute_code (shell strings and
`subprocess` argv lists with their `env`), and a local `python3 <script>.py` (the script is read). Allowed: a
disposable home (`HERMES_HOME=$(mktemp -d)`, a `tempfile.mkdtemp()` home, any other non-production path), a plain
`hermes serve`/`dashboard`. Refusal names the recipe; never copy credentials into the proof home (use
`token-broker`). Opt-out for a real (non-test) backend on the production home: `HERMES_SHARED_HOME_SERVE_OK=1`.
This is detection at the tool boundary; core's launch-time check is the enforcement boundary.

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

`tests/test_shared_home_serve.py` (gateway-9aaf151a15): the incident's `hub-admin '... HERMES_DESKTOP=1 hermes serve
--isolated ...'` and its variants (local, `ssh`, `bash -lc`, `python -m hermes_cli.main`, explicit production homes,
the caller's own home, `--profile`, `dashboard --isolated`) are refused from terminal and execute_code (argv list with
an inherited or `Path.home()/".hermes"` home, nested `terminal("hub-admin ...")`) and from a local liveproof-style
script; a local disposable `HERMES_HOME` does not excuse a remote launch. Disposable homes (`$(mktemp -d)`, a shell
var set from it, `export`, a scratch path, `tempfile.mkdtemp()`), plain `hermes serve`/`dashboard`, greps/echo/commit
messages, a missing script, the opt-out and the off switch are allowed. The Claude adapter path for this check is the
same `_pre_tool_call` and is not separately tested.
