# Kanban: refuse and flag ready cards no machine can claim (mirror 5494–5498)

## Behavior
- A card is structurally unclaimable when every machine carrying all its `runs_on` labels is `pinned_only` and would never copy a card from the card's home gateway (a `machine:<name>` pin counts only if that machine also advertises pickup), or when no machine carries all labels. `kanban_claimants` defines this; hold, drain, quota and offline states are deliberately not counted.
- Create-time checks (`check_create`) and moves (`MoveRefused("no_machine", ...)`, using the card's `created_on` home) refuse such label sets, and the create schema text now says labels carried only by `pinned_only` machines are refused. A dispatcher-tick sweep (`kanban_unclaimable_ready.safe_sweep`, run on primary-mode boards with a known registry) flags stranded ready, unclaimed cards: one chat notice per (card, labels) and, on the operator lane only, one open alarm card per class.
- The queue-position note carries `task_id` and a stranded-card text naming each machine's refusal reason; a recently closed alarm is not reopened for 6 h (`REFIRE_S`) while its cards stay stranded. 5497 changes only a show-queue test.
- 5495 and 5498 are test-only: they keep a legacy pickup exclusion proof under this admission, check that a ready row with live custody (parametrized field) is not flagged, and that a move check uses the card's home rather than the issuing gateway.

## How it works
`kanban_claimants.py` determines claimability; `kanban_move.py`, `kanban_runs_on_capabilities.py`, `kanban_chat_wait.py`, `kanban_parent_unclaimed.py`, `kanban_queue_position.py` and `tools/kanban_tools_schemas.py` consume it.

## Code
`hermes_cli/kanban_claimants.py`, `kanban_unclaimable_ready.py`, `kanban_move.py`, `kanban_queue_position.py`, `kanban_runs_on_capabilities.py`, `kanban_chat_wait.py`, `kanban_parent_unclaimed.py`, `tools/kanban_tools_schemas.py`.

## Tests
Source: `tests/hermes_cli/test_kanban_unclaimable_runs_on.py` (by test names: labels carried only by `pinned_only` machines are impossible, a machine's own cards stay claimable, a pin needs pickup, create and move refused, sweep flags once with one alarm, ignores claimable/unready cards, unknown registry flags nothing, a worker may reroute the card, dispatcher tick runs the sweep, queue note names the card and each refusal, closed alarm not reopened, only the operator lane files the alarm, live custody not flagged, move check uses card home); `test_kanban_worker_node.py` and `tests/tools/test_kanban_show_queue.py` are touched without new test functions. Not executed in this mirror pass.

## Applying
Apply in order.

## Gotchas
The sweep returns nothing for non-primary boards, an unreadable or empty registry, or an unknown own gateway, since absence of evidence is never treated as impossibility.

## plugin?
No.
