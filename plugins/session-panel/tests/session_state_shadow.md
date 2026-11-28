# P2 disposable-board classification shadow

`test_session_state_board_shadow.py` executes every immutable P0 scenario (17 IDs)
and five resolution variants against real disposable Kanban/replica databases and
SessionDB. It captures the **actual collector call used by SessionStateStore**,
then replays that exact evidence through legacy core `assemble`/`reduce_status`
and the legacy mods `model.reduce_panel`. The v2 result is read back from the
persisted store. Inputs, lifecycle events, outputs and every differing leaf are
retained as `p2_shadow` JUnit properties for archival after runner temp cleanup.

This is classification-boundary shadow proof, not an independent legacy Feed I/O
end-to-end proof or native Desktop proof. The legacy bundle projects the same
collected evidence into v1's id-keyed hydration; uniqueness is asserted before
conversion. V2 retains owner-qualified keys. Failure to adapt those keys initially
produced false missing-dependency differences; those discovery runs are not the
acceptance result. Requests use the P0 provider fixture; real ActionStore and
registered legacy-provider proofs remain the separate user-steps composed suite.

## Exact allowed differences

`session_state_shadow_allowlist.json` enumerates all 22 case IDs, an explanation
per case, and the exact old/new value at each changed leaf. No wildcard field or
whole row is ignored. Missing and null are distinct. It compares sections, groups,
counts, pips, core status kind and stripe, including row order, text, clock tokens,
owner-qualified action targets and request/choice bindings. Negative controls
reject added-null fields, changed scalar values, changed bindings and row order.

- Lifecycle-only running and ready rows lose uncertified stripe authority.
- Card/CI/deploy producer categories own the external stripe.
- Overdue triage is unresolved; fresh subagent execution is running.
- Stale running replicas retain the agent category in both row and pip.
- A committed own-request resolution removes only that ask, changes its row to
  waiting, and preserves `unblocked by you · <choice> · <when>`.
- Failed delivery and a new block episode remain unresolved: no allowed difference.
- A sibling request remains live with its exact binding.
- Resolving a shared closure-only parent clears that parent's group, not the two
  dependent cards; the hierarchy and dependency target remain intact.

The shared-parent variant exposed missing choice/time in the resolved group.
Core now renders those fields just as the resolved ordinary card does. The
composed test failed before the fix. Core's independent parametrized regression
covers label/action/fallback and wire/display time; label/action/wire mutants
fail its intended assertions. This does not alter execution authority or resolve
another request.

## Canonical invocation

Copy this plugin and shared `plugins/*.py` into an owned ignored fixture directory
under core `tests/hermes_cli/`, preserving `plugins/session-panel/tests/` layout.
Run core `scripts/run_tests.sh <copied test> --junitxml=<unique path>` with
`HERMES_TEST_FILE_RETRIES=0`, and aggregate `p2_shadow` properties: exactly 22 unique
IDs, 27 passing tests including coverage and rejection controls, no skips/errors.
Record both source heads and content hashes before publication. Remove only the
owned copied sources afterwards. Do not call a no-core skipped suite this proof.

Full P2 still requires the remaining clause audit, composed preservation suite,
canonical publication, independent exact-head review and required CI/landing.
P3 consumer cutover and P4 drafting/action fan-out remain separate phases.
