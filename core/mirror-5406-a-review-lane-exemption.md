# Review gate exemption without a local creator card

Mirror entries: 5118, 5119.

## Behavior

- Gate-exempt chat review titles can stay exempt when their creator card has no local row, including key-matched review filings.
- Named detector filings, non-exempt titles and reviews whose local creator is gate-held stay gated.
- Existing alarm/urgent handling remains unchanged.

## How it works

Lane gating distinguishes an absent creator row from a known gate-held creator. The follow-up corrects the overly broad idempotency-prefix exclusion: matching a `core-wt-review:` key alone must not defeat an otherwise exempt chat review. Detector creator identity remains a gating signal. Tests exercise the actual creation boundary with the configured review pattern.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_lanes.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Lane regressions cover key-matched chat ship reviews without a creator, named detector reviews and non-exempt-title controls, plus gate-held local creators. Source tests use the creation boundary rather than only synthetic task objects. Live dispatch, review approval validation and remote creator resolution are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_lanes.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

This changes lane admission, not review approval requirements. Missing local creator data does not turn a named detector into a chat filing. Idempotency namespaces are matching inputs, not proof that a filing is detector-owned.

## Why core instead of a plugin

Lane caps and creator-gate policy are core admission decisions shared by every card creator. A review UI/plugin cannot override them without disagreeing with authoritative scheduling.
