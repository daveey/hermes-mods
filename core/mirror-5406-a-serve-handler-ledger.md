# Update-progress handler ledger coverage

Mirror entries: 5047.

## Behavior

- The committed serve-handler inventory includes `GET /api/hermes/update/progress`.
- This entry changes test inventory only; it does not add or modify the route implementation.

## How it works

The generated handler ledger is refreshed so the existing handler-completeness check recognizes the update-progress route. The source mail identifies `scripts/gen_serve_handler_ledger.py` as the generator.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

No production code changes. This feature contains regression inventory/fixture changes only.

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

The source change is coverage data for `test_committed_ledger_lists_every_live_handler`. It addresses a missing-row failure, not endpoint response semantics. HTTP behavior, authentication and live update-progress polling are untested by this mirror task.

Source test/coverage paths:

- `tests/hermes_cli/serve_handler_ledger.json`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

A ledger row is not an endpoint integration test. Regenerate the ledger when handlers change rather than removing the completeness assertion.

## Why core instead of a plugin

The ledger inventories built-in HTTP handlers, so its completeness contract belongs beside the core handler tests rather than in a plugin.
