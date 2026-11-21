# Spend accounting excludes only proven pre-turn owner refusals

Mirror entries: 5292, 5293. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Owner-unavailable/down runs and crashed runs with integer exit 69 are excluded from spending-run counts only when they have no owner admission or usage receipt and no contradictory host-refusal phase. Admitted, interrupted and mid-turn owner-loss work still counts. Readback reports the excluded count separately.

## How it works

The spend scan reads outcome/metadata for eligible closed runs, joins usage by run ID and builds the `owner_admitted` event set. `_refused` rejects exclusion when usage/admission exists, or `host_refusal` is malformed or not explicitly `preadmission`. Exit code must be an integer 69, not bool or a loosely equal noninteger. Existing time-window/minimum-duration rules remain. CLI JSON includes `spent.excluded` and human output explains skipped owner-refusal runs.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_budget_cli.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests distinguish real runs from refusal streaks, retain real-run/token limits, verify excluded readback, parameterize refusal evidence and exercise actual mid-run loss/admitted exit-69/unadmitted streak bookings.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_spend_budget.py`
- `tests/hermes_cli/test_kanban_spend_budget_refusal.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

Neutral failure-budget booking does not prove zero spend. A missing usage receipt alone is not grounds to exclude an admitted run. This is budget classification, not a billing correction or proof that provider-side cost was zero.

## Why core instead of a plugin

The spend window depends on authoritative run/event/usage evidence used by core dispatch. A plugin-facing summary cannot safely change budget admission retroactively.
