# Shared account tool-store borrowing for fake-home runners

Mirror snapshot 4059; covered ordinals: 3928.

## Behavior

`scripts/run-in-hermes-env` can borrow the account's already pinned tool store for a fake home, matching `scripts/run_tests.sh`. Explicit runtime-store choices, real account/profile roots, own-store roots, incompatible pins, unusable stores, and account-seed opt-out retain the per-home behavior.

## How it works

The borrowing functions move from the test runner into `scripts/_activation.sh`: account-root discovery, opt-out parsing, and read-only `tool_store_pins.py` checks are shared. Successful borrowing exports `HERMES_RUNTIME_DIR` and reuses an existing writable uv Python cache only if it holds the pinned minor. The environment runner attempts borrowing only when its inherited activation is not already current; `activate` cleans up the added helper function names. The diagnostic goes to stderr.

The test runner keeps its isolated pytest child environment without the provisioning override; tools inherited through PATH are still shared, so writing through such tools can mutate the account store.

## Code

- `activate`
- `scripts/_activation.sh`
- `scripts/run-in-hermes-env`
- `scripts/run_tests.sh`
- `scripts/tool_store_pins.py`

### Mirrored commits

- 3928: `5350e203a85b0fed1a48e36ba485cba301b39716` — Shares account tool-store borrowing across fake-home shell runners.

## Tests

- `tests/scripts/test_tool_store_pins.py`: both entry points run through real shell activation with fake account discovery and a setup stub recording environment rather than downloading tools. Matching pins export the account runtime/Python stores and do not create a fake-home tool store; missing/incompatible pin facts export neither.
- `tests/pm/test_account_seed.py`: the shared borrowing helper consults opt-out and shell parsing matches package-manager opt-out semantics, including whitespace/case normalization.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

The pin check occurs before the package-manager install lock: a concurrent account update can change facts between check and install. Borrowing is not isolation from writes through PATH tools. This patch does not authorize modifying another user's store, and no provisioning or downloads were performed by this documentation pass.

## Plugin?

No. Shell bootstrap must choose its runtime store before plugins or the main interpreter can load.
