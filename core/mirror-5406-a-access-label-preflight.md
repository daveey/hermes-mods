# Respect Board-carried access labels in placement preflight

Mirror entries: 5120, 5121, 5122, 5123.

## Behavior

- Preflight removes mapped access needs already attested by a card's `runs_on` labels, avoiding a false empty-registry warning.
- Missing/malformed labels and non-sync schemas retain registry checks; other requirements are not broadly exempted.
- Existing `worker-first-v1` treatment remains unchanged; the Board still enforces carried labels when claiming.

## How it works

`_task_runs_on()` reads labels from the task object or persisted SQL column, parses string JSON only as a list/tuple, and returns no labels on unreadable/malformed input. `preflight()` consults the shared `ACCESS_LABELS` mapping and removes only needs whose corresponding label is actually carried.

Covered protocol needs include `hub:read`, `hub:write`, `ssh:mini` and `ssh:mbp`; these are capability identifiers, not example hostnames. Requirements such as cloud/provider access without a mapped carried label still go through routing.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_capability_routing.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Declared-access tests cover task-object and real persisted `runs_on`, each mapped need against an empty registry, missing-label controls, actual non-sync schema without the column, unparseable JSON, truncated lists and JSON object/string/null values. Tests assert warning/no-warning behavior without changing ready status. Actual Board claim enforcement and separately invoked tool/REST placement flows are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_declared_access.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Unreadable labels are no attestation: retain the registry check, do not fail open by returning every access label. Keep protocol labels literal when normalizing public examples. Avoid interpreting a JSON object's keys as carried labels.

## Why core instead of a plugin

Preflight must agree with the Board's core claim-label contract and persisted task data; a plugin-only registry tweak would hide warnings without establishing the same attestation boundary.
