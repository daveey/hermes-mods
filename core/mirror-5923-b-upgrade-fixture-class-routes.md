# Upgrade E2E fixtures: isolated class routes for Kanban admission

Mirror snapshot 5923; ordinal 5877.

Entries:

- **5877**: Configure isolated class routes for upgrade Kanban fixtures

## Behavior

- Kanban admission selects models through dynamic classes (`class:fast`, `class:standard`, `class:deep`), but the upgrade end-to-end fixtures only configured a concrete interactive model, so both baseline and replay-sender runs refused work with `routing_disabled` before any worker could launch.
- The fixtures now enable real class allocation using only a disposable loopback provider: a `model_routing` policy with one eligible metered route per class, plus a credential-pool entry pointing at the fixture's own base URL. Production admission code and the E2E assertions are unchanged.
- The helper asserts the base URL host is exactly `127.0.0.1` before writing anything; other endpoints raise and leave no `auth.json`.

## How it works

- `tests/e2e/core/upgrade/_install_helpers.py`: `configure_kanban_routing(home, base_url)` appends `model_routing: {...}` to the fixture `config.yaml` and writes `auth.json` with a single manual credential.
- `tests/e2e/core/upgrade/handoff/_handoff.py` and `tests/e2e/core/upgrade/pm/_pm.py` call it when building their fixture homes.

## Code

Shipped paths are test-support only (no production files):

- `tests/e2e/core/upgrade/_install_helpers.py`
- `tests/e2e/core/upgrade/handoff/_handoff.py`
- `tests/e2e/core/upgrade/pm/_pm.py`

## Tests

Source-side test shipped by this entry; not executed by this documentation pass, so no pass result is claimed here:

- `tests/e2e/core/upgrade/test_kanban_routing_fixture.py` (each class has a real route per `class_unroutable_reason`; disabling routing yields `routing_disabled`; non-fixture endpoints are rejected)

## Applying

This entry is part of the ordered snapshot 5923. Apply it only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). It is not an independent patch.

## Gotchas

- The credential is a placeholder valid only for the loopback fixture; never reuse this helper for a real provider.
- New upgrade fixtures that launch Kanban workers need this call too, or they will hit `routing_disabled` again.

## Plugin

Test-support change; no plugin or hook is involved.
