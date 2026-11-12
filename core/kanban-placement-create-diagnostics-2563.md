# Placement diagnostics explain structural rejection at create

Patches: **2524, 2525**.

## Behavior

- When a card is created on a gateway with no placement route and is not enrolled for worker-first placement, the reported reason now says why instead of the generic "no placement route for this gateway". Examples: `assignee not allowlisted`, `host workspace`, `uncarried option`, and for markers the evidence, e.g. `home gateway marker: mentions 'special-device'` or `local marker: ...`.
- `hermes kanban create` (text and `--json`) now reports the diagnostic: a `Placement: <why>` line in text, a `placement` object (`where`, `why`, plus `placement_policy` or `runs_on` when relevant) in JSON. An enrolled card reports `waiting_for_worker_capacity`; a caller-supplied `runs_on` reports "execution labels specified by the caller". An idempotent replay of an existing card reports nothing (a fresh decision is never attributed to an existing card).
- Placement decisions are unchanged; this is evidence only. Manual pins and courier creation keep the old fallback text. The CLI diagnostic judges the same resolved assignee the card is created with ([kanban-cli-create-resolved-assignee-2491.md](kanban-cli-create-resolved-assignee-2491.md)).
- If the diagnostic itself fails, the already committed create still succeeds and reports `portability diagnostic failed`.

## How it works

New module `hermes_cli/kanban_placement_diagnostics.py`. `portability(args, cfg)` is the former body of `portable_reason` in `hermes_cli/kanban_placement.py`, returning `(reason, evidence)` from one classifier pass so admission and explanation share ordering and exemptions (evidence and coordination filtering are never re-scanned). `portable_reason` now returns `portability(...)[0]`. `routeless_why` feeds the `_decide` fallback, except for manual `runs_on` and courier creation. `cli_creation`, `cli_json_fields` and `print_cli_creation` are called from `_cmd_create` in `hermes_cli/kanban.py` after the create; 2525 passes the resolved assignee as the keyword argument to `_cli_fields` and builds the fields inside the failure guard. See [kanban-worker-first-placement.md](kanban-worker-first-placement.md) and [kanban-creation-placement-audit-2409a.md](kanban-creation-placement-audit-2409a.md).

## Code

`hermes_cli/kanban.py`, `hermes_cli/kanban_placement.py`, `hermes_cli/kanban_placement_diagnostics.py` (new).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_placement_diagnostics.py`. Entry paths: the `kanban_create` tool handler (`kanban_tools._handle_create`) and the CLI slash form (`kanban.run_slash("create ...")`, text and `--json`) for rejection reasons and the `created` event's `portable_reason`; `kanban_placement.decide` and `portable_reason` directly for each structural reason, filtered evidence, coordination and worktree exemptions, manual/courier fallbacks and invalid extra markers; CLI idempotent replay, enrollment and manual labels; and diagnostic-failure isolation (`local_why` and `_cli_fields` forced to raise). REST create is untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- `kanban create` output gains a `Placement:` line and a `placement` JSON key; scripts parsing exact output should tolerate it.
- Evidence strings quote the matched text from the card, so they can contain card content.
- Cards already created are not re-diagnosed.

## Plugin

Core change; no plugin required.
