# Kanban block notices name the calling agent

Patches:

- `patches/2191-fix-kanban-attribute-CLI-block-notices-to-the-callin.patch`

A `hermes kanban block` made from an agent's tool subprocess was announced as `blocked — actor: user`, even though an agent filed it.

## Behavior

- `cli_actor()` derives an audit-only identity: `agent (chat <session>)` from `HERMES_SESSION_KEY`/`HERMES_SESSION_ID`, else `agent (task <id>)` from the worker markers, else `agent` when the CLI detects an agent-invoked context, else `user`. It is never an authorization signal.
- `block` and `unblock` record it as `actor` in the event payload; the local session notice, the gateway watcher notice and the remote (cross-host) notice print `actor: <actor>`; the human-request "Next action for the user" text is unchanged.
- Remote cards carry it as an optional `block_actor` field (string only; absent or non-string is ignored).

## How it works

`block_notice(..., actor=)` renders it; `blocked_actor_fields` reads the latest `blocked` event; `observe_owner_task` publishes it into the origin ledger; `kanban_remote_notices.format_notice` and `session_notifications` pass it through.

## Code

`hermes_cli/kanban_cli_actor.py` (new), `kanban_block_notice.py`, `kanban.py`, `kanban_db.py`, `kanban_human_request_migration.py`, `kanban_origin_ledger.py`, `gateway/kanban_watchers_notifier.py`, `tui_gateway/kanban_remote_notices.py`, `tui_gateway/session_notifications.py`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/hermes_cli/test_kanban_cli_actor.py`: marker matrix (none/session/worker/RPC markers), metadata-only block, local, remote and gateway notice text, unblock actor, precedence, malformed `block_actor` values. The human-shell control passes on the unpatched baseline too.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

Attribution is advisory and environment-derived; a human shell with exported agent variables reads as an agent. Old events without `actor` keep the old wording.

## Plugin

None.
