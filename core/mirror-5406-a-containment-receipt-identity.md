# Stable containment receipt identity

Mirror entries: 5057, 5058, 5075.

## Behavior

- Re-fetching a certified containment receipt tolerates changing feed-head/transport metadata and Board row re-stamps.
- Certified changes still refuse: payload, origin, proof and all row identity fields other than explicitly excluded server bookkeeping remain protected.
- The first evidence copy stays in the append-only cache.

## How it works

`remember()` compares its canonical key fields and a `_certified()` projection instead of the entire evidence JSON. The projection retains the event row and `containment_v1` proof, stripping only row `seq`, `hlc`, `updated_at` and `version`. Top-level transport fields such as `head` are not identity. Canonical stop/release certification and guard triggers remain unchanged.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_containment_replica.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Replica tests cover advancing feed head, Board row re-stamps and refusal on certified mutations. The review follow-up makes proof/origin/payload mutations valid enough to pass initial certification and specifically reach the identity comparison. Remote containment tests cover contain/resume with an advancing evidence head. Real service transport, live re-stamping and deployment are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_containment_remote.py`
- `tests/hermes_cli/test_kanban_containment_replica.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Do not drop all row fields or proof checks to tolerate transport churn. `created_at`, `run_uid`, deletion/order fields and every non-bookkeeping row field remain identity. This change does not make invalid canonical evidence acceptable.

## Why core instead of a plugin

The canonical receipt cache and database containment guards are core security/custody boundaries shared by sync and remote release, not a post-processing extension point.
