# Deterministic spend and archive timestamp fixtures

Mirror entries: 5063, 5094.

## Behavior

- Spend-promotion tests reuse the persisted stop timestamp instead of sampling another wall-clock second.
- Archive-continuation tests anchor comment windows to the persisted move timestamp, preserving strict post-move acceptance and same-second exclusion.
- These entries change tests only, not production timing or retry policy.

## How it works

The spend fixture takes the recorded stopping time as its reference. The archive fixture explicitly models zero-, one- and two-second comment-to-move gaps, then derives acceptance/exclusion timestamps from the stored move event. A failed new reference must still not fall back to an old archive reference.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

No production code changes. This feature contains regression inventory/fixture changes only.

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Source coverage lives in `test_kanban_spend_promote.py` and `test_kanban_continue_archive.py`, including `test_comments_after_move_and_no_old_fallback`. The archive parameter cases characterize the fixture race rather than prove any unrecorded historical interleaving. Production timing behavior and retry execution are untested by this mirror task.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_spend_promote.py`
- `tests/hermes_cli/test_kanban_continue_archive.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Do not loosen strict-greater-than production comparisons or add retries to hide a fixture that sampled the wrong clock. Test-only changes offer no new runtime guarantee.

## Why core instead of a plugin

The fixtures pin core spend/archive contracts and must live with those core regression suites; an installed plugin would not correct their timestamp references.
