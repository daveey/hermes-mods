# Commit-driven comment wakes without lost in-flight expedites

Mirror entries: 5051, 5110, 5111, 5130.

## Behavior

- A committed comment on a card parked on its own `wait-comment` probe makes that probe due now and wakes the dispatcher.
- The probe still decides whether the comment qualifies. A rejected comment costs one extra probe and then normal cadence resumes.
- A comment/status expedite committed during a running probe survives that probe's older non-waking result; ordinary unexpedited cadence stays exactly apply-time plus `check_every`.

## How it works

A TEMP trigger records comment inserts in the same transaction as the existing status recorder. Post-commit flush reads both recorders before any expedite write can clear them, updates matching scheduled parks and wakes the dispatcher. Rollback leaves no expedite, unrelated comments need no write, and expedite failures do not fail a committed comment.

`run_due_wake_checks` carries its claimed next slot into `apply_result`. Each non-waking result branch preserves a stored `next_at` strictly earlier than that slot, otherwise schedules the normal apply-time cadence. Command/generation fencing still drops results from an old park. The later fixture change supplies a real creator session/subscription instead of weakening subscriber admission.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_local_status_wake.py`
- `hermes_cli/kanban_wake_check.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

`test_kanban_comment_wake.py` covers answers, rejected/system comments, unrelated cards/non-comment parks, rollback, combined comment/status commits and best-effort failure. `test_kanban_wake_inflight_expedite.py` uses a second connection between probe and apply to cover comment and status races, every non-waking branch (including already-escalated scope-unknown), re-parking and skewed apply clocks. The fixture now models creator eligibility. Natural cross-host reply latency and live dispatcher delivery are untested by this mirror task.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_comment_wake.py`
- `tests/hermes_cli/test_kanban_wake_inflight_expedite.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Cross-card `wait-comment` parks intentionally keep their normal cadence; the commit hook does not scan every park command. Expedite is not unconditional unpark. Creator questions still require a real wakeable subscriber.

## Why core instead of a plugin

Correctness depends on database commit/rollback hooks, dispatcher wake signals and fenced probe result application. A plugin notification after a tool call misses synced writes and cannot prevent the in-flight overwrite.
