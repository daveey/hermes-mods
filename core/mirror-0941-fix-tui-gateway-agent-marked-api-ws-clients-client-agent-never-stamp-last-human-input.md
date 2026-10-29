# fix(tui_gateway): agent-marked /api/ws clients (?client=agent) never stamp last_human_input; their chats born hidden [daveey]

Patch 0941, source `e216b50f29c694bf7d45ea62c3bf729b972b326e`.

## Behavior

fix(tui_gateway): agent-marked /api/ws clients (?client=agent) never stamp last_human_input; their chats born hidden [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_classifier_rejects_relay_and_hosted_submits():`
- `Only client-sent submits count. Injected turns never reach the prompt.submit`
- `def record_human_input(session_id: str = "", home: Path | None = None) -> None:`
- `def _(rid, params: dict) -> dict:`
- `def _create_session(rid, params: dict, *, copy_parent_history: bool = False) ->`
- `def reset_transport(token) -> None:`
- `class WSTransport:`
- `def _disable_nagle(ws: Any) -> None:`
- `async def handle_ws(ws: Any, *, auth_identity: dict | None = None, subprotocol:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> gateway_rpc.py dials /api/ws?client=agent, but nothing read the marker: an agent's proof prompt.submit
> stamped desktop/last_human_input.json and hermes-sync deferred the core-update drain window as
> 'David active' while he was asleep (t_01a186c204de1d5b).
>
> - handle_ws records the client query marker on WSTransport.client_kind.
> - note_submit skips agent-marked transports and sessions such a connection created (agent_created).
> - session.create on an agent-marked connection defaults to hidden unless params say otherwise.

## Code

- `tui_gateway/human_input.py`
- `tui_gateway/methods_prompt.py`
- `tui_gateway/methods_session.py`
- `tui_gateway/transport.py`
- `tui_gateway/ws.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_human_input_record.py`

New or changed test definitions present in the source diff:

- `test_agent_marked_ws_submit_does_not_record`
- `test_normal_client_ws_submit_still_records`
- `test_submit_into_agent_created_session_does_not_record`
- `test_hidden_param_path_unchanged_on_normal_transport`
- `test_ws_accept_records_client_kind`
- `test_agent_connection_session_create_defaults_hidden`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
