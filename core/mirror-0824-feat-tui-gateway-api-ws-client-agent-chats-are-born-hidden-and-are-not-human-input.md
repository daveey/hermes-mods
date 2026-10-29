# feat(tui_gateway): /api/ws?client=agent chats are born hidden and are not human input [daveey]

Patch 0824, source `57401bf04599584efbec490ac4b9363ae8b4c2bf`.

## Behavior

feat(tui_gateway): /api/ws?client=agent chats are born hidden and are not human input [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `async def pty_ws(ws: WebSocket) -> None:`
- `async def gateway_ws(ws: WebSocket) -> None:`
- `+"""/api/ws?client=agent marks the connection's transport as an agent client; no marker = today. [daveey]`
- `def test_auto_continue_schedule_does_not_record(monkeypatch, home):`
- `Covers the two seams Bot Mode's "sessions are always hidden" policy leans on:`
- `def test_session_list_hides_internal_sources(db, source):`
- `desktop-ssh clients alike, in <HERMES_HOME>/desktop/last_human_input.json (a`
- `def is_human_typed_submit(rid, params: dict, text) -> bool:`
- `def _flag(params: dict, name: str) -> bool:`
- `def _create_session(rid, params: dict, *, copy_parent_history: bool = False) ->`
- `def current_transport() -> Optional[Transport]:`
- `class WSTransport:`
- `class _SendFailed(Exception):`
- `async def handle_ws(ws: Any, *, auth_identity: dict | None = None, subprotocol:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Agents driving the Desktop backend over /api/ws (proof cards, gateway_rpc.py) created throwaway chats
> that landed in the sidebar, and every agent prompt.submit stamped desktop/last_human_input.json, which
> defers hermes-sync core-update drain windows.
>
> - /api/ws reads the optional ?client=agent marker and stores client_kind="agent" on the WSTransport
>   (absent or any other value: unset, today's behaviour).
> - session.create over an agent transport without an explicit "hidden" is born hidden; an explicit
>   hidden: false still wins.
> - human_input.is_human_typed_submit() returns False for submits on an agent transport.
>
> The marker is client-declared and only picks defaults; it is not a security boundary. Auth, the
> renderer, sidebar filters, auto-continue and kanban notify are unchanged (a hidden agent chat still
> auto-continues and receives kanban notices; covered by a test).
>
> Kanban: t_01a184bf9b541962

## Code

- `hermes_cli/web_routers/chat_ws.py`
- `tui_gateway/human_input.py`
- `tui_gateway/methods_session.py`
- `tui_gateway/transport.py`
- `tui_gateway/ws.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_api_ws_client_kind.py`
- `tests/tui_gateway/test_human_input_record.py`
- `tests/tui_gateway/test_session_hidden_rpc.py`

New or changed test definitions present in the source diff:

- `test_client_agent_param_sets_agent_client_kind`
- `test_without_agent_param_client_kind_is_unset`
- `test_agent_transport_submit_does_not_record`
- `test_renderer_transport_submit_still_records`
- `test_classifier_rejects_agent_transport`
- `test_agent_transport_create_without_hidden_is_born_hidden`
- `test_agent_transport_explicit_hidden_false_stays_visible`
- `test_renderer_transport_create_stays_visible`
- `test_renderer_transport_explicit_hidden_true_still_hides`
- `test_hidden_agent_chat_still_auto_continues_and_gets_kanban_notices`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
