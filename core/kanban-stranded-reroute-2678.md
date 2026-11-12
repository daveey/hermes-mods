# Any worker can reroute a ready card stranded on impossible runs_on

Patches: **2658, 2659**.

## Behavior

- A ready, never-claimed card of this gateway whose stored `runs_on` labels no enrolled machine carries (rows written before the create-time label check, or an alias or typo) can never be leased. Any scoped worker may now repair it with `kanban_move(task_id=<card>, runs_on=[...], reason=...)`; the card's own creator may too (2659). Previously the worker was refused `not_owner` and asked a human to edit the card.
- Conditions, all required: the board is a Board primary; the card is on this gateway (created here or with no origin recorded); status `ready` with no claim lock, lease holder or current run; it has labels; the Board machine registry is known and has this machine's own entry; and no enrolled machine (held or inactive included) carries every label. An unknown registry is a refusal. A satisfiable placement is never touched. The new labels must still pass the normal move checks (an active, unheld machine must carry them).
- The move writes a comment (author `kanban-reroute`) keeping the old labels for undo, records `rerouted_by` and `stranded_runs_on` on the move event, and adds a "Superseded" comment to any blocked card whose `human_request` names the rerouted card. Those asks are never unblocked automatically.
- The `stranded_in_ready` diagnostic for such a card has title `Ready for <age>: runs_on [...] matches no enrolled machine`, `held_reason: impossible_runs_on`, and tells the worker to reroute it (CLI hint `hermes kanban move <id> --runs-on machine:<registered-name>`). The registry comes from the diagnostic's config or the process's already-cached registry; it never fetches from the network. Without a known registry the diagnostic is unchanged.
- The `not_owner` refusal text and the `kanban_move` tool schema name this route and the operator-op route for foreign cards.
- Ask lint refuses (`board_op_ask`) a `human_request` whose action only asks for a Kanban edit, move, complete or archive of a named card id, pointing at the agent routes instead; asks for approval, payment, sign-in or a physical step pass.

## How it works

- 2658: `kanban_runs_on_capabilities.impossible(labels, machines, me)` is the shared create-time check. `kanban_creator_repin` gets `stranded_refusal` (returns None when rerouting is allowed, else the reason), `_registry_view`, and `stranded_reroute` (calls `kanban_move.move_queued` with `event_extra`, then writes the comments). `kanban_block_policy` adds `board_op_ask_error` and the `board_op_ask` code to the ask-lint chain; `kanban_diagnostics` adds `_impossible_runs_on`; `tools/kanban_tools.py` and `tools/kanban_tools_schemas.py` carry the wording.
- 2659: the creator path of `creator_repin` called `stranded_refusal` only for non-creators, so the card's own creator got `not_repinnable`. It now tries `stranded_refusal` first and otherwise keeps its old refusal, now also naming the stranded condition.

## Code

`hermes_cli/kanban_creator_repin.py`, `hermes_cli/kanban_runs_on_capabilities.py`, `hermes_cli/kanban_block_policy.py`, `hermes_cli/kanban_diagnostics.py`, `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_stranded_reroute.py`. Entry path: the module API (`kanban_creator_repin` entry for the scoped `kanban_move` grant, `compute_task_diagnostics`, `user_ask_lint_error` and `ask_lint_check`) against a temporary Board-primary board with a fake registry. Covered: a worker rerouting an impossible-label card and the card then being claimed; the old labels kept in a comment and a blocked ask noted but left blocked; satisfiable cards, claimed cards, unsatisfiable new labels and an unknown registry refused; refusals for non-ready statuses (blocked, todo, scheduled, triage), a lease holder, a current run, a foreign `created_on` and a non-primary board; the creator rerouting its own stranded card but still refused on an ordinary satisfiable one; the diagnostic text and the `not_owner` text; `board_op_ask` refusals and real human asks passing. The `kanban_move` tool handler end to end (schema text, the real tool call path) and a live Board registry are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Restoring the old labels from the comment strands the card again; the comment says so.
- Cards created on another gateway are refused here; use a `kanban_supervisor_request` operator move ([kanban-operator-call-2563.md](kanban-operator-call-2563.md)).
- The `board_op_ask` pattern is deliberately narrow (card id, board verb first in the clause, a board term); other wordings can still pass.

## Plugin

Core change; no plugin required.
