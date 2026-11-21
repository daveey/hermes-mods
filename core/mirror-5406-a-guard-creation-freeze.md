# Authoritative freeze for declared guard self-work

Mirror entries: 5091, 5092, 5093.

## Behavior

- `kanban.self_work.freeze_guards` defaults to `true` at authoritative intake.
- Explicit `self_work.kind: guard` evidence appends to the oldest open guard owner in the same repository/area/tenant, across days; absent such an owner, creation receives definitive pre-write `self_work_guard_frozen` refusal.
- Existing execution and immutable receipt replay continue. Other maintenance kinds and undeclared cards are unchanged.

## How it works

The shared guard selector runs under intake write custody for both admission and append probing. It matches explicit declaration events, excludes done/archived owners, and orders by `created_at,id`. The switch must be boolean; malformed configuration does not unfreeze it. Create/replay entry adapters serialize the definitive refusal with `created:false` and `handoff_complete:false`. The authority loads its own configuration; sender declarations cannot override it.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_selfwork_guards.py`
- `hermes_cli/kanban_selfwork_intake.py`
- `hermes_cli/kanban_selfwork_owner.py`
- `website/docs/user-guide/configuration.md`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Guard tests cover shipped default, no-owner refusal/no writes, append and one-time replay, cross-day reuse, repository/area/tenant/kind isolation, closed owners, switch-off restoration, unrelated product/maintenance work, malformed switches and oldest-owner ordering independent of insertion/ID order. Entry tests cover intake refusal/append/create surfaces and definitive create-path response. Live authority configuration and deployment are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_selfwork_guard_entry.py`
- `tests/hermes_cli/test_kanban_selfwork_guards.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

No title heuristics classify legacy guards. Disable the switch at the intake authority, not in sender payloads, to restore ordinary area/day batching and creation. Reusing an open owner is not permission to reopen a closed guard.

## Why core instead of a plugin

Only core authoritative intake can make a pre-write decision consistent with immutable receipts and courier acknowledgements. Sender-side plugin heuristics cannot enforce repository/tenant ownership globally.
