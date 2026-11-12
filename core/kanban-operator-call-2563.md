# Scoped operator call on one foreign card

Patches: **2533, 2534**.

## Behavior

- A gateway's live board holds only its own cards, so `kanban_comment`, `kanban_move` and `kanban_unblock` on a card another gateway created refuse `foreign_card`. The refusal message now names the supported route: `kanban_supervisor_request(ops=[{op: 'operator', action, task_id, ...}])`.
- Exactly one operator op per request, on one named foreign card, with `action`: `comment {text}` (max 8000 chars), `move {runs_on, reason}` (not while the card runs) or `unblock {reason}`. The card's home gateway applies it and logs a result event; the caller's request then finishes and wakes the parked caller. complete, archive, link, unlink, block, edit, assign, delete and reclaim are refused as owner-only; `label_publish` is not offered yet.
- Unblock is allowed only for a block placed by the card's own agent run (latest `blocked` event actor is `agent (task <target>)`); Desktop, dashboard, human-shell, chat-agent and policy holds, and anything waiting on a human, are refused.
- Limits: 3 calls in flight and 20 per 24 h per caller card. Refused for the caller's own card, cards local to this gateway, terminal cards, unknown cards and unsynced boards. A request expires after 24 h if the home never answers. Every call, accepted or refused, is written to an `operator_calls` log on the gateway that saw it.
- The caller accepts a result only if it names the home as executor and the Board's row evidence says the home gateway wrote it; forged copies are ignored and logged, and the call waits while the Board cannot vouch. No Board admin key is read.
- CLI: `hermes kanban supervisor-requests` gains `--id`, `--settled` (exit 0 once the request is done, rejected or failed; usable as a `wake_check`) and `--operator-log` (list the log, optionally filtered with `--task`). The tool's `next_step` suggests that wake check for operator calls.

## How it works

New `hermes_cli/kanban_operator_call.py`. Request side (the caller's non-worker supervisor drain): `normalize_op`, `preflight_op`, then `start` appends `operator_call_requested` on the caller's own card and parks the request in phase `awaiting_home`; `poll` finishes it from the home's `operator_call_result`. Home side (`apply_incoming`, run from `drain` each dispatcher tick): scans new replica events past a watermark, proves the caller (run was live, Board lease holder and event `origin_gateway` match), applies through the ordinary functions, and writes the result on the target card. 2534 anchors the watermark on the Board uid of the event at it (not the replica inode) so a rebuilt replica is rescanned, adds the positive-provenance `_agent_block` check and vouched results, and records an `operator_call_moved` marker for the worker-first repin path. `move_queued` gains `event_extra` for provenance. Wiring: `kanban_supervisor_requests.py` (`normalize_ops`, `preflight`, `_execute`, `drain`, `submit` logs refusals, `cli_command`), `kanban_parser.py`, `kanban_foreign_card.py`, the tool schema in `tools/kanban_tools_schemas.py` and `tools/kanban_tools_graph.py`. See [kanban-supervisor-requests.md](kanban-supervisor-requests.md) and [kanban-foreign-card-replica.md](kanban-foreign-card-replica.md).

## Code

`hermes_cli/kanban_operator_call.py` (new), `hermes_cli/kanban_supervisor_requests.py`, `hermes_cli/kanban_move.py`, `hermes_cli/kanban_foreign_card.py`, `hermes_cli/kanban_parser.py`, `tools/kanban_tools_graph.py`, `tools/kanban_tools_schemas.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_operator_call.py`. Entry path: the module API `kanban_supervisor_requests.submit` and `drain` across a simulated caller gateway, Board and home gateway (comment, move including repin, unblock gates, owner-only refusals, one card per call, origin and forged-result checks, replay idempotence, unreachable Board, parked-caller wake, evicted caller copy, deadline, worker-context execution refused, no admin-key access, rebuilt-replica rescan). The `kanban_supervisor_request` tool handler, the CLI flags (`--id`, `--settled`, `--operator-log`) and a real Board are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Requires a board that syncs to the Board; otherwise the call is refused `not_synced`.
- The `operator_calls` and `operator_call_scan` tables are created on demand (`ensure_schema`) with `CREATE TABLE IF NOT EXISTS`; 2534 changed the scan table's `replica_ino` column to `last_uid`, so a table created from the 2533 definition alone is not altered.
- Completion is asynchronous: the caller should park with the suggested `wake_check`.

## Plugin

Core change; no plugin required.
