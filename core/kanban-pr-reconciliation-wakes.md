# PR waits wake for reconciliation, not automatic acceptance

Patches: **0339 0343 0344 0346**.

## Behavior

An explicit immediate PR checkpoint can resume without a guard notice re-arming its own hold (0339). A later real PR update still protects against duplicate work; non-immediate or superseded continuations do not gain blanket permission.

The PR condition probe wakes a parked card when the PR becomes terminal, including closed-unmerged, so the worker can reconcile it (0343). An open PR can also wake on explicitly supplied, verified supersession evidence (0344). Neither wake completes the card nor substitutes for required exact-head CI or acceptance.

## How it works

`kanban_db_dispatch_pr_state.py` distinguishes real checkpoint/handoff events from guard-generated notices. `kanban_pr_wake.py` uses the existing profile-scoped PR acceptance transport. Its exit contract is **0 inspect now, 1 pending, 2 error**. Invalid exact PR URLs and unreadable/mismatched evidence are errors; transport failures do not print sensitive stderr.

Optional `--superseding-commit` and `--verification-job` must be supplied together as an exact lowercase 40-hex SHA and a positive decimal job ID. The successful completed job must match that SHA, and the repository's default branch must contain it. The message explicitly requires replacement ownership and all required exact-head CI before delivery. 0346 rejects malformed completed-job conclusions rather than treating malformed evidence as an ordinary pending job.

## Code

`hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_pr_wake.py`; source user guidance in `website/docs/user-guide/features/kanban.md`.

## Tests

Source suites, **not run in this documentation pass**:

- `tests/hermes_cli/test_kanban_pr_continuation.py`: immediate checkpoint dispatch; guard notice cannot supersede real handoff; non-immediate/newer-event controls.
- `tests/hermes_cli/test_kanban_pr_wake.py`: open/merged/closed states, invalid identifiers without normalization, transport timeout/error redaction, real test-board wake without completion.
- `tests/hermes_cli/test_kanban_pr_supersession.py`: exact successful job/head, containing default branch, invalid argument pair before transport, malformed evidence/conclusions, abandoned-PR controls.

Run with `bash scripts/run_tests.sh <files>` in the isolated applied checkout. Mocked API tests are not a live GitHub or broker exercise. Full-series apply/test verification is pending the mirror owner.

## Applying

Apply the ordered series with prior condition wakes and PR acceptance transport. [kanban-pr-park-handoff.md](kanban-pr-park-handoff.md) documents the later 0349 orderly-park handoff and broker-aware state-read changes. [kanban-origin-work-ledger.md](kanban-origin-work-ledger.md) documents deferred origin completion notices, a separate boundary.

## Gotchas

Closed-unmerged is a reason to inspect, not evidence of delivery. A single successful job plus ancestry wakes reconciliation only; it does not certify every required check. Do not normalize malformed identifiers or infer supersession from similar PR titles.

## Plugin relationship

These are core dispatcher/probe semantics. A plugin may present results but cannot confer PR acceptance or override a protected continuation by emitting a notice.
