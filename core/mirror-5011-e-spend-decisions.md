# Scoped spend decisions and home-owned budget enforcement

Mirror entries: 4771, 4772, 4807, 4812.

## Behavior

A spend stop can be released by its explicitly named decision card into a finite additional run window. Home boards enforce budgets even for work they cannot execute locally. Proven owners of qualifying open core publications can be spared spend triage without resetting their accounting.

## How it works

The spend_promote operator action accepts runs 1–20, the current stop episode, an earlier decision comment and a reason. Authenticated caller scope is checked at request time and again in the home transaction. Event ordering, later human comments, human holds and open parents gate the decision. One transaction records the budget override and triage-to-ready promotion, opening exactly the requested run window; request markers make replay idempotent even after completion. Dispatch applies home-owned no-progress/spend checks to placement-refused rows too. Publication sparing requires owning-run publication evidence and cached raw OPEN/main PR details, read only once over budget and outside the writer; the locked phase rechecks custody and state and records one notice per window.

## Code

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_admission.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_db_dispatch_preclaim.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_pr_owner.py`
- `hermes_cli/kanban_spend_promote.py`
- `tools/kanban_tools_schemas.py`
- `website/docs/user-guide/features/kanban.md`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Spend-promote tests cover exact windows, replay, foreign homes, superseded episodes, comment ownership/order, later human comments, bounds, parent gates and rollback. Home-budget tests cover ready/review placement refusals, dry-run and foreign-copy controls. Publication-owner tests distinguish genuine publication from mentions/reviews, handle state refresh/reopening, and retain no-progress priority.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_active_pr_awaited_head.py`
- `tests/hermes_cli/test_kanban_home_budget_dispatch.py`
- `tests/hermes_cli/test_kanban_park_invariants.py`
- `tests/hermes_cli/test_kanban_spend_pr_owner.py`
- `tests/hermes_cli/test_kanban_spend_promote.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Unblock is not a spend decision, zero is not an exemption through spend_promote, and sparing does not release an existing triage hold. A worker can lease before the home next checks, allowing an extra run. Publication eligibility is narrowly repository-scoped by source policy; this is not a general exemption for every open pull request, and its GitHub observation uses the existing five-minute cache.
