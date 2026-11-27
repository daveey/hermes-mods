# Kanban release command and delegate scope renewal

Patches: 6107, 6108, 6128, 6129, 6130, 6131, 6137, 6138, 6139.

## Behavior

- A delegate renewal authority lets a chat session a human has granted rights renew scope stops it does not own: `hermes kanban delegate grant|revoke|list` (human-only, default window, bounded lifetime, wildcard bounded tighter). On a synced board the grant is canonical Board state.
- `hermes kanban release` composes the existing steps (mandate comment, scope renewal, spend budget, promote) for a held card in one operation, resolves a decision card to its stopped card, refuses unsafe/contained/not-held cases before writing, and can resume an untouched earlier release.
- A Desktop Unblock click releases only a current-stop offer backed by this board's newest own escalation or re-file record, with matching ask digest when recorded. Supplied request stamps are stripped. A dead offer is re-filed at most once per park episode only when this board recorded filing an ask in that episode; other dead offers pass the normal unowned-stop escalation gates instead. Re-files use stranded_park_refiled and do not send a second desk notice.
- Agent releases retain the cache-read limit's own attribution, including an override's deference to the board limit and unknown older attribution. They refuse a retained cache-read limit already reached in the current window, even with no run limit. A human can lift it; exempt cards remain exempt. Directly restating limits with spend-budget attributes them to that caller; this is not a provenance-security boundary.
- An agent must acknowledge the exact newest comment id in the unread set printed by the refusal with --seen, including on resume; a larger id is not accepted, an unsynced comment cannot be acknowledged, and --seen when no reading is needed is refused. Human callers proceed without that acknowledgement.
- Release records stop_released in the deciding transaction; that event, not a mandate-looking comment, makes a completed rerun already_released. Retried mandates are reused only if they postdate relevant scope evidence. Resumption cannot promote a card moved back to triage for another reason, and a decision card for an older pending-stop episode is refused.

## How it works

kanban_release, kanban_scope_delegate, kanban_scope_renewal(_cli), kanban_card_answer and kanban_stranded_brief wire the commands. Validated CLI/tool requests drop release_target, but the synced human_request column alone is not trusted: Board writes can carry raw requests. A release offer must also match the home-origin filing record and, when present, its ask digest.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_card_answer.py`
- `hermes_cli/kanban_human_request.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_release.py`
- `hermes_cli/kanban_scope_delegate.py`
- `hermes_cli/kanban_scope_renewal.py`
- `hermes_cli/kanban_scope_renewal_cli.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_budget_cli.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_spend_promote.py`
- `hermes_cli/kanban_spend_reviewer.py`
- `hermes_cli/kanban_stranded_brief.py`
- `hermes_cli/kanban_stranded_park.py`
- `hermes_cli/kanban_sync_hub_routes.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `tests/hermes_cli/test_kanban_real_board_delegate.py`
- `tests/hermes_cli/test_kanban_release.py`
- `tests/hermes_cli/test_kanban_scope_delegate.py`
- `tests/hermes_cli/test_kanban_spend_promote.py`
- `tests/hermes_cli/test_kanban_spend_reviewer.py`
- `tests/hermes_cli/test_kanban_stranded_park.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- The human check for grants is process provenance, not authentication.
- Release uses the delegate lookup in its pre-check so a granted chat is not refused early.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the Kanban CLI, card answers and authority checks inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
