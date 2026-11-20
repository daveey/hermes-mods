# Open incident reuse and closed-generation history

Mirror entries: 4510, 4511.

## Behavior

- **4510** — Repeated pickup observations append deduplicated evidence to an open exact-subject repair incident; closed incidents do not cover new firings.
- **4511** — Flaky-test intake keeps closed cards closed, selects an open daily generation or allocates the first unused generation suffix, and reconciles lost acknowledgments first.

## How it works

Pickup filing selects an open incident by detector and subject prefix within a write transaction. Identical comment bodies from the detector are not appended twice; new firings with distinct evidence append. New incidents retain the positive decimal identity suffix required by the alarm protocol, and existing placement/source-exclusion handling remains.

Flake intake resolves the exact daily key plus numeric generation suffixes. A cached destination must match known identity; unknown statuses or unavailable identity refuse rather than guessing. An exact payload already present on a previously closed cached card acknowledges the pending page without creating a duplicate. Otherwise an open generation is reused or a new generation is created. A closure racing selection leaves the page pending for the next attempt.

## Code

- `docs/ci-flaky-intake.md`
- `hermes_cli/kanban_pickup_stall.py`
- `scripts/ci/flaky_intake.py`

## Tests

Source tests cover identical/distinct pickup evidence, distinct detector subjects and immediate replacement of done/archived incidents. Flake tests cover fresh generations, exact-subject matching, invalid status/cache refusal, closed-card lost-ack reconciliation and real CLI/board delivery. They are not newly executed here.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/ci/test_flaky_intake.py`
- `tests/ci/test_flaky_intake_incidents.py`
- `tests/ci/test_flaky_intake_real_board.py`
- `tests/hermes_cli/test_kanban_pickup_stall.py`
- `tests/hermes_cli/test_pickup_incident_append.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

These changes preserve incident history, not reopen completed work. They do not change retry thresholds, cadence, red-main policy or self-heal rules. Broader maintenance producer rollout remains outside the source changes documented here.
