# Hide and retire agent-started proof chats, keep provenance owner-only

Mirror entries: 5421, 5422, 5423.

## Behavior

- A CLI chat started by an agent process is hidden from human session lists at creation, while keeping its CLI surface and all owner/terminal authorization gates.
- `hermes chat --visible` or `HERMES_CHAT_VISIBLE=1` keeps an agent-started chat visible.
- Hidden agent-started proof chats are archived when ended, marked done, or idle for 24 hours, independently of the `auto_archive` setting. Human, manually hidden and explicitly visible chats are left untouched.
- The marker and hidden flag survive context compression: continuations inherit them, so compressed proof lineages still retire and never surface in the sidebar.
- The session-create contract gains boolean `hidden` and `agent_cli` parameters; a non-boolean value is refused.

## How it works

Classification uses start-time process ancestry (including a forwarded agent marker), not the running process's own advertised environment, so a human CLI is not mistaken for an agent. Inconclusive ancestry stays visible.

For gateway-backed creates, the flags are part of the frozen create request, so they are bound to the create receipt identity: a retry with the same request returns the same session, and a retry with a different `hidden` value is refused. The row is inserted with `hidden` and an `_agent_cli` model-config marker in the same transaction, rather than hidden after commit. Classic in-process creates and `/branch` use a helper that writes the marker and hidden flag after create.

Entry 5422 makes the classic path write the marker through the seed model-config call instead of the general patch call (keeping provenance on the owner's creation path), and refreshes the generated gateway contract files for `agent_cli`.

The retirement sweep runs ahead of the draft sweep and is not gated by `auto_archive`. It selects hidden, unarchived CLI/TUI/oneshot rows carrying the marker (hidden alone never implies authorship), excludes compression parents whose child is authoritative, and skips guarded ids.

Entry 5423 adds the marker to the conversation-lifetime model-config keys and, in the compression publication transaction, copies the parent's hidden value to a continuation that carries the marker.

## Code

- `hermes_cli/chat_visibility.py`, `hermes_cli/_parser.py`, `hermes_cli/main.py`
- `hermes_cli/gateway_chat.py`, `hermes_cli/gateway_session_store.py`
- `hermes_cli/cli_session_mixin.py`, `hermes_cli/cli_commands_mixin.py`
- `gateway/session_policy.py`, `gateway/session_controls.py`, `hermes_state_local.py`
- `hermes_state_agent_cli.py`, `hermes_state_maintenance.py`, `hermes_state_compression.py`
- `tui_gateway/contracts/canonical_sessions.py`
- `apps/shared/src/gateway-contract.generated.ts`, `apps/shared/src/gateway-contract.openrpc.json`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

The source tests cover: ancestry-driven create parameters (agent marker variants, human, explicit visible, inconclusive ancestry); a real parent-environment subprocess check; `--visible` parsing; atomic hidden create with retry binding and refusal of a changed `hidden`; retirement of ended, done and idle proofs; and real compression publication for ended/done/idle/active and hidden/visible combinations. Existing launch tests were updated for the two new create parameters. The classic `/branch` helper path, the seed-call change in 5422 and the generated contract files have no dedicated test in these patches (untested).

Source test paths:

- `tests/gateway/test_agent_cli_visibility.py`
- `tests/hermes_cli/test_agent_cli_visibility.py`
- `tests/hermes_state/test_agent_cli_retirement.py`
- `tests/hermes_state/test_agent_cli_compression.py`
- `tests/hermes_cli/test_gateway_chat.py`, `tests/hermes_cli/test_empty_toolset_launches.py`
- `tests/hermes_cli/test_gateway_chat_update_wait.py`, `tests/hermes_cli/test_stream_json.py`

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

- Hidden alone does not retire a chat; the agent marker is required.
- Inconclusive ancestry fails open to visible.
- Authorship does not grant terminal or owner authority; those gates are unchanged.
- A gateway whose `runtime.describe` does not list `agent_cli`/`hidden` among create parameters is an older contract.

## Why core instead of a plugin

Create receipts, the sessions schema, the retirement sweep and compression publication are core transactions; visibility must be committed atomically with the row and survive rotation, which a plugin cannot guarantee.
