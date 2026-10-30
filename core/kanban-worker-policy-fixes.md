# Kanban capability, PR hold, origin-ledger and relay-ask fixes

Patches:

- `patches/1087-fix-kanban-declared-queue-only-assignees-skip-the-pr.patch`
- `patches/1088-fix-kanban-scope-PR-respawn-holds-to-comment-writer-.patch`
- `patches/1090-fix-kanban-release-origin-obligations-accepted-at-an.patch`
- `patches/1102-fix-kanban-refuse-a-human-ask-that-only-relays-a-mes.patch`

Four small Kanban policy fixes.

## Behavior

- 1087: assignees declared queue-only skip the profile capability check.
- 1088: PR respawn holds are scoped to comment writer runs.
- 1090: an origin obligation accepted at an already terminal, verified owner version is stored released, and `refresh_observations` heals legacy rows left unreleased.
- 1102: the `kanban_block`/`kanban_create` ask lint refuses a human ask that only relays a message to an agent (code `agent_relay_ask`), pointing at `needs=[...]`, `cross_session` or an owner card; real asks (approve, pay, sign in, physical, A/B) still pass.

## How it works

Each is a narrow predicate change in the dispatcher, ledger or ask lint.

## Code

`hermes_cli/kanban_capabilities.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_origin_ledger.py`, `hermes_cli/kanban_block_policy.py`, `hermes_cli/kanban_capability_routing.py`.

## Tests

`tests/hermes_cli/test_kanban_capabilities.py` (1087), `test_kanban_dependency_review.py`, `test_kanban_pr_comment_provenance.py`, `test_kanban_pr_continuation.py` and `kanban_guard_helpers.py` (1088), `test_kanban_origin_terminal_accept.py` (1090), `tests/tools/test_kanban_agent_relay_ask.py` (1102). Entry paths of the ask lint other than the one that test drives are untested.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.
