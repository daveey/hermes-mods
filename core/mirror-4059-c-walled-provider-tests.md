# Walled Desktop route refusal test pins provider ownership and logs

Mirrored entries: 3875.

## Behavior

The Desktop wall-reroute regression test now explicitly covers a known other provider’s endpoint and refusal logging. An endpoint belonging to another provider must not be treated as merely unknown and deferred. This is a test-only extension of existing routing policy.

## How it works

The existing must-not-move parameterized test adds an other-provider endpoint case, verifies provider_owns_route returns false for it, and records log_not_deferred calls. Expected key/endpoint reasons are checked for the corresponding refusal cases rather than only asserting the final routing outcome.

## Code

These entries modify only the test files listed below; production behavior is unchanged.

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/agent/test_walled_default_pin.py`: Asserts the endpoint is known not to belong to the selected provider, the wall pin still refuses where required, and only the expected key/endpoint refusal reasons are logged.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

False ownership and unknown ownership are different states. This patch does not add a new provider, change credentials or establish a live Desktop reroute; endpoint tests use fixture overrides.

## Plugin?

No new plugin; tests pin core routing and diagnostics.
